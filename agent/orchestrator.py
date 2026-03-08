"""
Main LangChain Orchestrator Agent.
Phase 1 stub — agent skeleton without full tool/prompt wiring.
Full implementation in Phase 2.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from utils.message_guard import can_send


async def invoke_agent(
    user_id: str,
    trigger_reason: str,
    user_message: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Main entry point for the Orchestrator Agent.
    Called by the scheduler or webhook handler.

    Phase 1: Checks the message guard and logs the invocation.
    Phase 2: Builds the LangChain agent and runs it.
    """
    if session is None:
        return

    if not await can_send(user_id, session):
        return  # Hard cap reached or silent hour

    # Phase 2 will instantiate the LangChain agent here.
    # For now, just log that the agent was invoked.
    import logging
    log = logging.getLogger(__name__)
    log.info(
        "Agent invoked | user=%s | trigger=%s | message=%s",
        user_id,
        trigger_reason,
        user_message,
    )
