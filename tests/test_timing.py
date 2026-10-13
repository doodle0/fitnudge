"""Daily window computation, habit learning, and the tick — all on a frozen clock."""
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest

from agent import dispatch
from db import queries as db
from timing.learn import learn_minutes
from timing.tick import run_tick
from timing.windows import check_window, window_for
from utils.time_utils import KST, today_kst

TODAY = today_kst()


def at(hour: int, minute: int = 0, day=TODAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=KST)


def fake_user(**kw):
    return SimpleNamespace(default_departure_time=kw.get("usual"), rest_weekdays=kw.get("rest", []))


def monday_of_this_week():
    return TODAY - timedelta(days=TODAY.weekday())


# ---------------------------------------------------------------- window_for (pure)

def test_default_window_weekday_vs_weekend():
    monday = monday_of_this_week()
    assert window_for(monday, fake_user(), None).start == at(18, 30, monday)
    saturday = monday + timedelta(days=5)
    w = window_for(saturday, fake_user(), None)
    assert w.start == at(11, 0, saturday) and w.source == "default" and w.end - w.start == timedelta(hours=1)


def test_usual_time_beats_default_and_learned_beats_usual():
    monday = monday_of_this_week()
    assert window_for(monday, fake_user(usual=time(20, 0)), None).source == "usual"
    profile = SimpleNamespace(learned_minutes=[19 * 60 + 15] + [None] * 6)
    w = window_for(monday, fake_user(usual=time(20, 0)), profile)
    assert w.start == at(19, 15, monday) and w.source == "learned"


# ---------------------------------------------------------------- learning (pure)

def _session(day, hour, minute=0, reported_day=None):
    created = datetime.combine(reported_day or day, time(hour, minute), tzinfo=KST)
    return SimpleNamespace(date=day, created_at=created)


def test_learn_minutes_median_minus_two_hours_with_day_type_fallback():
    monday = monday_of_this_week() - timedelta(days=7)
    sessions = [
        _session(monday, 21, 0), _session(monday - timedelta(days=7), 22, 0),   # Mondays: 21:00, 22:00
        _session(monday + timedelta(days=2), 21, 30),                           # one Wednesday
        _session(monday + timedelta(days=1), 9, 0, reported_day=monday + timedelta(days=2)),  # "어제" → skipped
    ]
    learned, samples = learn_minutes(sessions)
    assert samples == 3
    assert learned[0] == 19 * 60 + 30            # median(21:00, 22:00) − 2h
    assert learned[2] == 19 * 60 + 30            # Wednesday: 1 sample → weekday median of all 3 (21:30) − 2h
    assert learned[5] is None and learned[6] is None   # no weekend data


def test_learn_minutes_clamps_to_daytime():
    monday = monday_of_this_week() - timedelta(days=7)
    learned, _ = learn_minutes([_session(monday, 6, 0), _session(monday - timedelta(days=7), 7, 0)])
    assert learned[0] == 8 * 60


# ---------------------------------------------------------------- check_window + tick (DB)

@pytest.fixture
def captured_runs():
    runs = []

    async def runner(user_id, trigger):
        runs.append((user_id, trigger))

    dispatch.set_runner(runner)
    yield runs
    dispatch.set_runner(None)


async def _user(session, user_id):
    return await db.get_user(session, user_id)


@pytest.mark.db
async def test_window_open_inside_and_closed_outside(session, user_id):
    await db.update_user_fields(session, user_id, default_departure_time=time(19, 0))
    user = await _user(session, user_id)
    assert (await check_window(session, user, at(19, 10)))[0] is True
    is_open, reason, _ = await check_window(session, user, at(18, 50))
    assert not is_open and "outside window" in reason


@pytest.mark.db
async def test_window_closed_in_quiet_hours(session, user_id):
    await db.update_user_fields(session, user_id, default_departure_time=time(22, 30))
    is_open, reason, _ = await check_window(session, await _user(session, user_id), at(22, 40))
    assert not is_open and "quiet hours" in reason


@pytest.mark.db
async def test_window_closed_on_rest_day_snooze_workout_and_pending_followup(session, user_id):
    from scheduler import jobs

    await db.update_user_fields(session, user_id, default_departure_time=time(19, 0))
    now = at(19, 10)

    await db.update_user_fields(session, user_id, rest_weekdays=[now.weekday()])
    assert "rest day" in (await check_window(session, await _user(session, user_id), now))[1]
    await db.update_user_fields(session, user_id, rest_weekdays=[])

    await db.set_proactive_snooze(session, user_id, now + timedelta(hours=12))
    assert "snoozed" in (await check_window(session, await _user(session, user_id), now))[1]
    await db.set_proactive_snooze(session, user_id, None)

    await jobs.schedule_user_followup(user_id, now + timedelta(hours=1), "x", session)
    assert "pending" in (await check_window(session, await _user(session, user_id), now))[1]
    await jobs.cancel_user_followup(user_id, session)

    await db.insert_workout_session(session, user_id, ["등"])
    assert "already worked out" in (await check_window(session, await _user(session, user_id), now))[1]


@pytest.mark.db
async def test_tick_fires_once_per_day(session, user_id, captured_runs):
    await db.update_user_fields(session, user_id, default_departure_time=time(19, 0))
    assert await run_tick(now=at(19, 5)) == [user_id]
    assert await run_tick(now=at(19, 10)) == []          # already fired today
    (uid, trigger), = captured_runs
    assert trigger.kind == "scheduled" and trigger.proactive and "19:00–20:00 (usual)" in trigger.reason


@pytest.mark.db
async def test_tick_skips_users_who_did_not_opt_in(session, user_id, captured_runs):
    await db.set_opted_in_proactive(session, user_id, "discord", False)
    await db.update_user_fields(session, user_id, default_departure_time=time(19, 0))
    assert await run_tick(now=at(19, 5)) == [] and captured_runs == []
