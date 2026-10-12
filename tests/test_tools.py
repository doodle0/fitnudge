"""Tool handlers called directly (no model): delivery, guards, workouts, memory."""
import pytest

from agent.tools import RunState, build_tools
from agent.triggers import Trigger
from db import queries as db

pytestmark = pytest.mark.db


def tools_for(user_id: str, trigger: Trigger) -> dict:
    return {t.name: t.handler for t in build_tools(RunState(user_id=user_id, trigger=trigger))}


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


async def test_send_message_delivers_logs_and_counts(session, user_id, fake_channel):
    tools = tools_for(user_id, Trigger.user_message())
    result = await tools["send_message"]({"text": "오늘 등 어때요? 💪"})
    assert "is_error" not in result
    assert fake_channel.sent == [("1001", "오늘 등 어때요? 💪")]
    assert await db.get_daily_counts(session, user_id) == (0, 1)
    turns = await db.get_recent_turns(session, user_id)
    assert turns[-1].role == "agent" and turns[-1].proactive is False


async def test_send_message_per_run_cap(session, user_id, fake_channel):
    tools = tools_for(user_id, Trigger.user_message())
    for i in range(2):
        await tools["send_message"]({"text": f"msg {i}"})
    third = await tools["send_message"]({"text": "msg 2"})
    assert third.get("is_error") and "per run" in text_of(third)
    assert len(fake_channel.sent) == 2


async def test_send_message_rejects_more_than_three_sentences(session, user_id, fake_channel):
    tools = tools_for(user_id, Trigger.user_message())
    result = await tools["send_message"]({"text": "좋아요! 무릎은 어때요? 아프면 쉬어요. 계속 아프면 병원 가봐요."})
    assert result.get("is_error") and "Rewrite" in text_of(result)
    assert fake_channel.sent == [] and await db.get_daily_counts(session, user_id) == (0, 0)


async def test_recipient_unavailable_disables_proactive(session, user_id, fake_channel):
    fake_channel.unavailable = True
    tools = tools_for(user_id, Trigger.user_message())  # reactive: independent of quiet hours
    result = await tools["send_message"]({"text": "하이"})
    assert result.get("is_error")
    identity = await db.get_identity_for_user(session, user_id, "discord")
    await session.refresh(identity)
    assert identity.opted_in_proactive is False


async def test_record_workout_updates_streak_without_duplicate_memory(session, user_id, fake_channel):
    tools = tools_for(user_id, Trigger.user_message())
    result = await tools["record_workout"]({"muscle_groups": ["등", "이두"], "notes": "풀업 5개 첫 성공"})
    assert "Current streak: 1 days" in text_of(result)
    assert "This week: 1/3" in text_of(result)
    workout = await db.get_workout_session_today(session, user_id)
    assert workout.muscle_groups == ["등", "이두"] and workout.agent_notes == "풀업 5개 첫 성공"
    assert text_of(await tools["memory_read"]({"days": 1})) == "(no entries)"  # no duplicate memory


async def test_memory_write_rejects_unknown_kind(session, user_id):
    tools = tools_for(user_id, Trigger.user_message())
    result = await tools["memory_write"]({"text": "x", "kind": "diary"})
    assert result.get("is_error")


async def test_schedule_followup_validation_error_is_returned_to_model(session, user_id):
    tools = tools_for(user_id, Trigger.user_message())
    result = await tools["schedule_followup"]({"in_minutes": 1, "reason": "x"})
    assert result.get("is_error") and "at least 5 minutes" in text_of(result)
