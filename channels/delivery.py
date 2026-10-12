"""
Outbound delivery: the only path by which the agent's text reaches a user.
"""
from __future__ import annotations

import contextlib
import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from channels import base
from config import settings

log = logging.getLogger(__name__)


async def deliver(session: AsyncSession, user_id: str, text: str, *, proactive: bool) -> None:
    """Send `text` on the user's active channel, then log the turn and count it."""
    from db import queries as db
    from utils import message_guard

    user = await db.get_user(session, user_id)
    if user is None:
        raise LookupError(f"Unknown user {user_id}")
    identity = await db.get_identity_for_user(session, user_id, user.active_channel)
    if identity is None:
        raise base.RecipientUnavailable(f"User has no identity on channel '{user.active_channel}'.")

    channel = base.get(user.active_channel)
    try:
        await channel.send(identity.external_id, text)
    except base.RecipientUnavailable:
        await db.set_opted_in_proactive(session, user_id, user.active_channel, False)
        log.warning("Recipient unavailable; proactive messages disabled | user=%s", user_id)
        raise

    await db.insert_conversation_turn(session, user_id, role="agent", content=text, proactive=proactive)
    await message_guard.record_send(session, user_id, proactive=proactive)
    if settings.log_content:
        log.info("Sent | user=%s | proactive=%s | %s", user_id, proactive, text)
    else:
        log.info("Sent | user=%s | proactive=%s | chars=%d", user_id, proactive, len(text))


@contextlib.asynccontextmanager
async def typing(session: AsyncSession, user_id: str) -> AsyncIterator[None]:
    """Typing indicator on the user's active channel; silently no-op if unavailable."""
    from db import queries as db

    cm: contextlib.AbstractAsyncContextManager = contextlib.nullcontext()
    try:
        user = await db.get_user(session, user_id)
        identity = user and await db.get_identity_for_user(session, user_id, user.active_channel)
        if identity is not None:
            cm = base.get(user.active_channel).typing(identity.external_id)
    except LookupError:
        pass
    async with cm:
        yield
