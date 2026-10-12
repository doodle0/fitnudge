import pytest

from agent.context import build_context
from agent.triggers import Trigger
from db import queries as db
from memory import store as memory

pytestmark = pytest.mark.db


async def test_context_includes_memory_workouts_and_conversation(session, user_id):
    await memory.write(session, user_id, "10/14(화) 21시에 헬스장 가기로 함 (등 운동)", "commitment")
    await memory.write(session, user_id, "오른쪽 무릎 통증", "injury")
    await db.insert_workout_session(session, user_id, ["하체"])
    await db.insert_conversation_turn(session, user_id, "user", "오늘 21시에 갈게")

    ctx = await build_context(session, user_id, Trigger.user_message())

    assert "[commitment]" in ctx and "21시에 헬스장 가기로 함" in ctx
    assert "[injury] 📌" in ctx
    assert "하체" in ctx and "worked out today: yes (하체)" in ctx
    assert "user: 오늘 21시에 갈게" in ctx
    assert "kind: user_message (reactive)" in ctx
    assert "Reply with send_message" in ctx


async def test_first_contact_and_proactive_instructions(session, user_id):
    ctx = await build_context(session, user_id, Trigger.user_message(first_contact=True))
    assert "first_contact: yes" in ctx and "/start" in ctx

    ctx = await build_context(session, user_id, Trigger.followup("pre-reminder for 21:00"))
    assert "(proactive)" in ctx and "pre-reminder for 21:00" in ctx and "end without sending" in ctx
