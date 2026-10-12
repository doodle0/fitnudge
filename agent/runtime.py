"""
Orchestrator runtime on the Claude Agent SDK.

One run = one fresh SDK session: build the context snapshot from Postgres, let the agent call
our in-process MCP tools, tear down. SDK session resume is deliberately not used — Postgres is
the only source of truth.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
)

from agent.context import build_context
from agent.prompts.system import build_system_prompt
from agent.tools import SEND_MESSAGE, SERVER_NAME, RunState, build_tools, mcp_name
from agent.triggers import Trigger
from config import settings

log = logging.getLogger(__name__)

# An empty working directory: the CLI subprocess has no project files to see.
_SANDBOX_DIR = Path(tempfile.gettempdir()) / "fitnudge-agent-cwd"


@dataclass
class RunResult:
    ran: bool
    sent: list[str]
    skipped_reason: str = ""
    cost_usd: float | None = None
    num_turns: int | None = None
    error: str | None = None


def _cli_env() -> dict[str, str]:
    config_dir = Path(settings.claude_config_dir).resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if settings.anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    return env


def _transcripts_root() -> Path:
    return Path(settings.claude_config_dir).resolve() / "projects"


def purge_transcript(session_id: str) -> None:
    """Delete one run's CLI transcript. They hold conversation content; Postgres is our record."""
    try:
        uuid.UUID(session_id)  # also keeps glob metacharacters out
    except ValueError:
        return
    for path in _transcripts_root().glob(f"*/{session_id}.jsonl"):
        path.unlink(missing_ok=True)
    for path in _transcripts_root().glob(f"*/{session_id}"):
        shutil.rmtree(path, ignore_errors=True)


def purge_stale_transcripts() -> int:
    """Sweep transcripts left by crashed or timed-out runs. Returns the number of files removed."""
    root = _transcripts_root()
    if not root.exists():
        return 0
    cutoff = time.time() - settings.transcript_max_age_minutes * 60
    removed = 0
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if removed:
        log.info("Purged %d stale agent transcript files", removed)
    return removed


def _guard_hook(state: RunState):
    """PreToolUse hook: deny send_message when policy forbids it (the tool re-checks)."""
    from db.models import AsyncSessionLocal
    from utils import message_guard

    async def hook(hook_input: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            decision = await message_guard.check_send(
                session, state.user_id, proactive=state.trigger.proactive, sent_this_run=len(state.sent)
            )
        if decision.allowed:
            return {}
        log.info("send_message denied by hook | user=%s | %s", state.user_id, decision.reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }
        }

    return hook


def build_options(state: RunState) -> ClaudeAgentOptions:
    tools = build_tools(state)
    server = create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)
    _SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    return ClaudeAgentOptions(
        model=settings.agent_model,
        effort=settings.agent_effort,
        system_prompt=build_system_prompt(),
        tools=[],                                   # no built-in Claude Code tools (Bash, Read, Web…)
        mcp_servers={SERVER_NAME: server},
        strict_mcp_config=True,
        allowed_tools=[mcp_name(t.name) for t in tools],
        permission_mode="dontAsk",                  # anything not pre-approved is refused
        setting_sources=[],                         # never load filesystem settings / CLAUDE.md
        hooks={"PreToolUse": [HookMatcher(matcher=mcp_name(SEND_MESSAGE), hooks=[_guard_hook(state)])]},
        max_turns=settings.agent_max_turns,
        cwd=str(_SANDBOX_DIR),
        env=_cli_env(),
    )


async def run(user_id: str, trigger: Trigger) -> RunResult:
    """Run the orchestrator once for a user. Never raises."""
    from db.models import AsyncSessionLocal
    from utils import message_guard

    state = RunState(user_id=user_id, trigger=trigger)
    try:
        async with AsyncSessionLocal() as session:
            if trigger.proactive:
                decision = await message_guard.check_send(session, user_id, proactive=True)
                if not decision.allowed:
                    log.info("Proactive run skipped | user=%s | %s", user_id, decision.reason)
                    return RunResult(ran=False, sent=[], skipped_reason=decision.reason)
            prompt = await build_context(session, user_id, trigger)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to prepare run | user=%s", user_id)
        return RunResult(ran=False, sent=[], error=repr(exc))

    log.info("Agent run start | user=%s | trigger=%s", user_id, trigger.kind)
    result = RunResult(ran=True, sent=state.sent)
    session_id: str | None = None
    try:
        async with asyncio.timeout(settings.agent_timeout_seconds):
            async with ClaudeSDKClient(options=build_options(state)) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        session_id = message.session_id
                        result.cost_usd = message.total_cost_usd
                        result.num_turns = message.num_turns
                        if message.is_error:
                            result.error = f"{message.subtype}: {message.errors or message.result}"
    except Exception as exc:  # noqa: BLE001 — a failed run must never crash the caller
        log.exception("Agent run failed | user=%s | trigger=%s", user_id, trigger.kind)
        result.error = repr(exc)
    finally:
        if session_id:
            purge_transcript(session_id)

    log.info("Agent run end | user=%s | sent=%d | turns=%s | cost=%s | error=%s",
             user_id, len(state.sent), result.num_turns, result.cost_usd, result.error)
    return result

