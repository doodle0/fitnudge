"""
Main LangChain Orchestrator Agent.
Phase 2: full build_agent() + invoke_agent() with create_tool_calling_agent.

Note: CLAUDE.md specifies create_react_agent, but Claude has native tool-use support
and create_tool_calling_agent is more reliable — the ReAct text parser breaks on
single-parameter non-string tools (JSON object passed as raw string to int field).
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from utils.message_guard import can_send

log = logging.getLogger(__name__)


def build_agent(user_id: str):
    """
    Construct and return an AgentExecutor for the given user.
    Tools are pre-bound to user_id and session via the factory.
    Uses create_tool_calling_agent (Claude's native tool use) instead of
    create_react_agent to avoid text-parsing fragility with typed parameters.
    """
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_anthropic import ChatAnthropic

    from agent.prompts.orchestrator_system import build_orchestrator_prompt
    from agent.tools import get_all_tools

    llm = ChatAnthropic(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        temperature=0.7,
        api_key=settings.anthropic_api_key,
    )

    tools = get_all_tools(user_id)
    prompt = build_orchestrator_prompt()

    agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=10,
        handle_parsing_errors=True,
    )


async def _build_user_context(user_id: str, session: AsyncSession) -> str:
    from db import queries as db
    from utils.time_utils import now_kst

    user = await db.get_user(session, user_id)
    if user is None:
        return "User not found."

    streak = await db.get_streak(session, user_id)
    today_workout = await db.get_workout_session_today(session, user_id)
    message_count = await db.get_daily_message_count(session, user_id)
    pending_followup = await db.get_scheduled_followup(session, user_id)

    return (
        f"User: {user.kakao_nickname}\n"
        f"Weekly goal: {user.weekly_goal_count} sessions/week\n"
        f"Current streak: {streak.current_streak if streak else 0} days "
        f"(longest: {streak.longest_streak if streak else 0})\n"
        f"Last workout: {streak.last_workout_date if streak else 'none'}\n"
        f"Worked out today: {'Yes' if today_workout else 'No'}\n"
        f"Messages sent today: {message_count} / {settings.max_daily_messages}\n"
        f"Pending follow-up: "
        f"{pending_followup.reason if pending_followup else 'None'} "
        f"at {pending_followup.scheduled_for if pending_followup else 'N/A'}\n"
        f"Current local time (KST): {now_kst().strftime('%H:%M')}\n"
        f"Today's date: {date.today().isoformat()}"
    )


async def invoke_agent(
    user_id: str,
    trigger_reason: str,
    user_message: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Main entry point for the Orchestrator Agent.
    Called by the scheduler, webhook handler, or /internal/trigger.
    """
    if session is None:
        log.warning("invoke_agent called without a session — skipping.")
        return

    if not await can_send(user_id, session):
        log.info("Agent suppressed | user=%s | reason=cap_or_silent_hour", user_id)
        return

    try:
        user_context = await _build_user_context(user_id, session)
    except Exception:
        log.exception("Failed to build user context for user=%s", user_id)
        return

    input_parts = [f"Trigger: {trigger_reason}"]
    if user_message:
        input_parts.append(f"User message: {user_message}")
    input_parts.append(f"\nUser context:\n{user_context}")
    agent_input = "\n".join(input_parts)

    log.info("Invoking agent | user=%s | trigger=%s", user_id, trigger_reason)

    try:
        executor = build_agent(user_id)
        await executor.ainvoke({"input": agent_input})
    except Exception:
        log.exception("Agent execution failed | user=%s | trigger=%s", user_id, trigger_reason)
