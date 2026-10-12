"""
Entry points that wake the agent: inbound messages and proactive triggers.

Runs are serialized per user. If triggers arrive while a run is in progress they coalesce into
a single follow-up run (the newest user message wins; it sees every turn anyway).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from agent.triggers import Trigger
from channels.base import InboundMessage
from config import settings

log = logging.getLogger(__name__)

Runner = Callable[[str, Trigger], Awaitable[object]]


async def _default_runner(user_id: str, trigger: Trigger) -> object:
    from agent import runtime
    from channels import delivery
    from db.models import AsyncSessionLocal

    if trigger.proactive:
        return await runtime.run(user_id, trigger)
    async with AsyncSessionLocal() as session:
        async with delivery.typing(session, user_id):
            return await runtime.run(user_id, trigger)


_runner: Runner = _default_runner
_pending: dict[str, Trigger] = {}
_active: dict[str, asyncio.Task] = {}


def set_runner(runner: Runner | None) -> None:
    """Swap the agent runner (tests). None restores the real one."""
    global _runner
    _runner = runner or _default_runner


def submit(user_id: str, trigger: Trigger) -> asyncio.Task | None:
    """
    Queue a run for the user. Returns the task that will execute it, or None if it was merged
    into an already-pending run.
    """
    if user_id in _active:
        current = _pending.get(user_id)
        if current is None or trigger.priority() >= current.priority():
            _pending[user_id] = trigger
        return None
    task = asyncio.create_task(_drain(user_id, trigger), name=f"agent-run-{user_id}")
    _active[user_id] = task
    return task


async def _drain(user_id: str, trigger: Trigger) -> None:
    try:
        next_trigger: Trigger | None = trigger
        while next_trigger is not None:
            try:
                await _runner(user_id, next_trigger)
            except Exception:  # noqa: BLE001
                log.exception("Runner crashed | user=%s", user_id)
            next_trigger = _pending.pop(user_id, None)
    finally:
        _active.pop(user_id, None)


async def handle_inbound(msg: InboundMessage) -> asyncio.Task | None:
    """Persist an inbound message, extract memory from it, then queue a reactive run."""
    from agent.extract import extract_and_store
    from db import queries as db
    from db.models import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        user, _identity, created = await db.get_or_create_user_for_identity(
            session, msg.channel, msg.external_user_id, msg.display_name
        )
        user_id = str(user.id)
        previous = await db.get_recent_turns(session, user_id, limit=1)
        turn = await db.insert_conversation_turn(session, user_id, role="user", content=msg.text)

    if settings.log_content:
        log.info("Inbound | user=%s | %s", user_id, msg.text)
    else:
        log.info("Inbound | user=%s | chars=%d | new_user=%s", user_id, len(msg.text), created)

    last_bot = previous[-1].content if previous and previous[-1].role == "agent" else None
    await extract_and_store(user_id, turn.id, msg.text, last_bot)
    return submit(user_id, Trigger.user_message(first_contact=created))


async def wait_idle(timeout: float = 30.0) -> None:
    """Wait until no runs are active (tests, graceful shutdown)."""
    async with asyncio.timeout(timeout):
        while _active:
            await asyncio.gather(*list(_active.values()), return_exceptions=True)
