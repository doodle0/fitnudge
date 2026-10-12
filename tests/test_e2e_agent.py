"""
End-to-end: the real Claude Agent SDK + bundled CLI, our MCP tools and hooks, a real database,
and a scripted fake Messages API instead of Claude. No network, no cost.
"""
from pathlib import Path

import pytest

from agent import runtime
from agent.triggers import Trigger
from config import settings
from db import queries as db
from tests.fake_anthropic import FakeMessagesAPI, result_text, text, tool_results, tool_use

pytestmark = [pytest.mark.db, pytest.mark.e2e]

EXPECTED_TOOLS = {
    "mcp__fitnudge__send_message", "mcp__fitnudge__memory_read", "mcp__fitnudge__memory_write",
    "mcp__fitnudge__get_workout_log", "mcp__fitnudge__record_workout",
    "mcp__fitnudge__schedule_followup", "mcp__fitnudge__cancel_followup",
}


@pytest.fixture
async def fake_api(monkeypatch, tmp_path):
    apis: list[FakeMessagesAPI] = []

    async def make(script):
        api = FakeMessagesAPI(script)
        await api.start()
        apis.append(api)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", api.base_url)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
        monkeypatch.setattr(settings, "claude_config_dir", str(tmp_path / "claude-config"))
        monkeypatch.setattr(settings, "agent_timeout_seconds", 90)
        return api

    yield make
    for api in apis:
        await api.stop()


async def test_reactive_run_records_workout_and_replies(session, user_id, fake_channel, fake_api):
    await db.insert_conversation_turn(session, user_id, "user", "ㅇㅇ 오늘 등이랑 이두 했어")
    api = await fake_api([
        [tool_use("record_workout", muscle_groups=["등", "이두"])],
        [tool_use("send_message", text="등이랑 이두까지 오늘도 해냈네요 🔥 수고했어요!")],
        [text("done")],
    ])

    result = await runtime.run(user_id, Trigger.user_message())

    assert result.error is None, result.error
    assert fake_channel.sent == [("1001", "등이랑 이두까지 오늘도 해냈네요 🔥 수고했어요!")]
    assert (await db.get_workout_session_today(session, user_id)).muscle_groups == ["등", "이두"]

    first = api.agent_requests[0]
    assert {t["name"] for t in first["tools"]} == EXPECTED_TOOLS   # no built-in Claude Code tools
    assert first["model"] == settings.agent_model
    system = first["system"] if isinstance(first["system"], str) else " ".join(b["text"] for b in first["system"])
    assert "FitNudge companion" in system
    assert "ㅇㅇ 오늘 등이랑 이두 했어" in str(first["messages"])     # context snapshot reached the model
    assert "Current streak: 1 days" in result_text(tool_results(api.agent_requests[1])[0])
    transcripts = list((Path(settings.claude_config_dir) / "projects").rglob("*.jsonl"))
    assert transcripts == []                                         # deleted when the run ended


async def test_per_run_cap_blocks_third_parallel_send(session, user_id, fake_channel, fake_api):
    await db.insert_conversation_turn(session, user_id, "user", "안녕")
    api = await fake_api([
        [tool_use("send_message", text="하나"), tool_use("send_message", text="둘"),
         tool_use("send_message", text="셋")],
        [text("done")],
    ])

    result = await runtime.run(user_id, Trigger.user_message())

    assert result.error is None, result.error
    assert len(fake_channel.sent) == 2
    results = tool_results(api.agent_requests[1])
    blocked = [r for r in results if "per run" in result_text(r)]
    assert len(results) == 3 and len(blocked) == 1


@pytest.fixture
def no_quiet_hours(monkeypatch):
    """Proactive tests must not depend on the wall clock."""
    monkeypatch.setattr(settings, "quiet_start", 0)
    monkeypatch.setattr(settings, "quiet_end", 0)


async def test_proactive_run_sends_and_schedules(session, user_id, fake_channel, fake_api, no_quiet_hours):
    api = await fake_api([
        [tool_use("send_message", text="퇴근했어요? 오늘 등 어때요? 💪 피곤하면 쉬어도 돼요."),
         tool_use("schedule_followup", in_minutes=60, reason="no response nudge")],
        [text("done")],
    ])

    result = await runtime.run(user_id, Trigger.followup("evening check"))

    assert result.error is None, result.error
    assert len(fake_channel.sent) == 1
    assert await db.get_daily_counts(session, user_id) == (1, 0)
    assert (await db.get_recent_turns(session, user_id))[-1].proactive is True
    followup = await db.get_scheduled_followup(session, user_id)
    assert followup is not None and followup.reason == "no response nudge"
    assert "proactive" in str(api.agent_requests[0]["messages"])


async def test_proactive_run_skipped_without_opt_in(session, user_id, fake_channel, fake_api, no_quiet_hours):
    await db.set_opted_in_proactive(session, user_id, "discord", False)
    api = await fake_api([[tool_use("send_message", text="운동 어때요?")]])

    result = await runtime.run(user_id, Trigger.followup("no response nudge"))

    assert result.ran is False and "opted in" in result.skipped_reason
    assert api.agent_requests == [] and fake_channel.sent == []


def test_purge_stale_transcripts(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setattr(settings, "claude_config_dir", str(tmp_path))
    project = tmp_path / "projects" / "-tmp-fitnudge-agent-cwd"
    project.mkdir(parents=True)
    old, fresh = project / "old.jsonl", project / "fresh.jsonl"
    old.write_text("{}"), fresh.write_text("{}")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    assert runtime.purge_stale_transcripts() == 1
    assert not old.exists() and fresh.exists()
