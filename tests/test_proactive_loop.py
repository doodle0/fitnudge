"""Commitments, the check-in chain, snooze, ladder stop, profile updates — tool and job level."""
from datetime import time, timedelta

import pytest

from agent import dispatch
from agent.tools import RunState, build_tools
from agent.triggers import Trigger
from config import settings
from db import queries as db
from scheduler import jobs
from utils.time_utils import now_kst

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def no_quiet_hours(monkeypatch):
    """These tests use the real clock for scheduling, so quiet hours must not interfere."""
    monkeypatch.setattr(settings, "quiet_start", 0)
    monkeypatch.setattr(settings, "quiet_end", 0)


@pytest.fixture
def captured_runs():
    runs = []

    async def runner(user_id, trigger):
        runs.append((user_id, trigger))

    dispatch.set_runner(runner)
    yield runs
    dispatch.set_runner(None)


def tools_for(user_id, trigger=None):
    return {t.name: t.handler for t in build_tools(RunState(user_id=user_id, trigger=trigger or Trigger.user_message()))}


def text_of(result):
    return result["content"][0]["text"]


def hhmm(dt):
    return f"{dt:%H:%M}"


async def test_commit_plan_schedules_pre_reminder_30_min_before(session, user_id):
    commitment = (now_kst() + timedelta(hours=2)).replace(second=0, microsecond=0)
    result = await tools_for(user_id)["commit_plan"]({"at": hhmm(commitment), "plan": "등 운동"})
    assert "Reminder scheduled" in text_of(result)
    followup = await db.get_scheduled_followup(session, user_id)
    assert followup.kind == "pre_reminder" and followup.reason == "등 운동"
    assert followup.scheduled_for == commitment - timedelta(minutes=30)
    assert followup.commitment_at == commitment


async def test_commit_plan_too_soon_for_reminder_schedules_checkin(session, user_id):
    commitment = (now_kst() + timedelta(minutes=20)).replace(second=0, microsecond=0)
    result = await tools_for(user_id)["commit_plan"]({"at": hhmm(commitment), "plan": "하체"})
    assert "Check-in scheduled" in text_of(result)
    followup = await db.get_scheduled_followup(session, user_id)
    assert followup.kind == "checkin" and followup.scheduled_for == commitment + timedelta(minutes=15)


async def test_pre_reminder_fires_then_checkin_is_chained(session, user_id, captured_runs):
    commitment = now_kst() + timedelta(minutes=40)
    await jobs.schedule_commitment(user_id, commitment, "등 운동", session)

    await jobs._invoke_agent_job(user_id, "등 운동")      # what APScheduler calls at T-30

    (_, trigger), = captured_runs
    assert trigger.followup_kind == "pre_reminder" and trigger.commitment_at == commitment
    assert "planned \"등 운동\"" in trigger.describe()
    followup = await db.get_scheduled_followup(session, user_id)
    assert followup.kind == "checkin" and followup.scheduled_for == commitment + timedelta(minutes=15)


async def test_no_checkin_chained_after_workout_recorded(session, user_id, captured_runs):
    commitment = now_kst() + timedelta(minutes=40)
    await jobs.schedule_commitment(user_id, commitment, "등 운동", session)
    await db.insert_workout_session(session, user_id, ["등"])
    await jobs._invoke_agent_job(user_id, "등 운동")
    assert await db.get_scheduled_followup(session, user_id) is None


async def test_record_workout_cancels_pending_followup(session, user_id, fake_channel):
    await jobs.schedule_commitment(user_id, now_kst() + timedelta(hours=2), "등 운동", session)
    result = await tools_for(user_id)["record_workout"]({"muscle_groups": ["등"]})
    assert "follow-up was cancelled" in text_of(result)
    assert await db.get_scheduled_followup(session, user_id) is None


async def test_snooze_blocks_proactive_and_cancels_followup(session, user_id):
    from utils import message_guard

    await jobs.schedule_commitment(user_id, now_kst() + timedelta(hours=2), "등 운동", session)
    result = await tools_for(user_id)["snooze_proactive"]({"days": 0, "reason": "회식"})
    assert "No proactive messages until" in text_of(result)
    assert await db.get_scheduled_followup(session, user_id) is None
    decision = await message_guard.check_send(session, user_id, proactive=True)
    assert not decision.allowed and "snoozed" in decision.reason
    assert (await message_guard.check_send(session, user_id, proactive=False)).allowed   # replies unaffected

    await tools_for(user_id)["snooze_proactive"]({"days": -1, "reason": "back"})
    assert (await message_guard.check_send(session, user_id, proactive=True)).allowed


async def test_ladder_finished_blocks_more_nudges(session, user_id):
    for _ in range(settings.max_unanswered_proactive):
        await db.insert_conversation_turn(session, user_id, "agent", "운동 어때요?", proactive=True)
    result = await tools_for(user_id, Trigger.followup("no reply"))["schedule_followup"](
        {"in_minutes": 60, "reason": "no reply nudge"})
    assert result.get("is_error") and "finished" in text_of(result)


async def test_update_profile_fields(session, user_id):
    tools = tools_for(user_id)
    assert "토,일" in text_of(await tools["update_profile"]({"field": "rest_days", "value": "주말(토, 일)"}))
    assert "20:30" in text_of(await tools["update_profile"]({"field": "usual_time", "value": "20:30"}))
    assert "4" in text_of(await tools["update_profile"]({"field": "weekly_goal", "value": "4"}))
    assert (await tools["update_profile"]({"field": "weekly_goal", "value": "30"})).get("is_error")
    user = await db.get_user(session, user_id)
    await session.refresh(user)
    assert user.rest_weekdays == [5, 6] and user.default_departure_time == time(20, 30)
    assert user.weekly_goal_count == 4
