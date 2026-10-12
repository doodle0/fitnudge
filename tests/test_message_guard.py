from datetime import datetime

import pytest

from db import queries as db
from utils import message_guard
from utils.time_utils import KST

pytestmark = pytest.mark.db

DAYTIME = datetime(2026, 10, 13, 18, 30, tzinfo=KST)
NIGHT = datetime(2026, 10, 13, 23, 0, tzinfo=KST)


async def check(session, user_id, proactive=True, sent=0, now=DAYTIME):
    return await message_guard.check_send(session, user_id, proactive=proactive, sent_this_run=sent, now=now)


async def test_reactive_reply_allowed_at_night(session, user_id):
    assert (await check(session, user_id, proactive=False, now=NIGHT)).allowed


async def test_proactive_blocked_in_quiet_hours(session, user_id):
    decision = await check(session, user_id, now=NIGHT)
    assert not decision.allowed and "quiet hours" in decision.reason


async def test_proactive_requires_opt_in(session, user_id):
    await db.set_opted_in_proactive(session, user_id, "discord", False)
    decision = await check(session, user_id)
    assert not decision.allowed and "opted in" in decision.reason


async def test_per_run_cap(session, user_id):
    assert not (await check(session, user_id, proactive=False, sent=2)).allowed


async def test_daily_proactive_cap(session, user_id):
    for _ in range(4):
        await db.increment_daily_count(session, user_id, proactive=True)
    decision = await check(session, user_id)
    assert not decision.allowed and "daily proactive limit" in decision.reason


async def test_unanswered_proactive_stops_until_user_replies(session, user_id):
    for _ in range(3):
        await db.insert_conversation_turn(session, user_id, "agent", "운동 어때요?", proactive=True)
    assert not (await check(session, user_id)).allowed
    await db.insert_conversation_turn(session, user_id, "user", "ㅇㅇ 갈게")
    assert (await check(session, user_id)).allowed


async def test_reactive_replies_do_not_count_as_unanswered(session, user_id):
    for _ in range(3):
        await db.insert_conversation_turn(session, user_id, "agent", "답장", proactive=False)
    assert (await check(session, user_id)).allowed


async def test_no_nudge_after_workout(session, user_id):
    await db.insert_workout_session(session, user_id, ["등", "이두"])
    decision = await check(session, user_id)
    assert not decision.allowed and "already worked out" in decision.reason
