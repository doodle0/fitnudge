"""
Hard daily message cap — enforced in code, independent of the LLM.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from utils.time_utils import is_silent_hour


async def can_send(user_id: str, session: AsyncSession) -> bool:
    """
    Return True only if the user can receive another message right now.
    Checks both the daily cap and the silent-hour rule.
    """
    from db import queries as db

    if is_silent_hour(settings.agent_silent_after_hour):
        return False

    count = await db.get_daily_message_count(session, user_id)
    return count < settings.max_daily_messages


async def increment(user_id: str, session: AsyncSession) -> None:
    """Increment the daily message counter for the user."""
    from db import queries as db

    await db.increment_daily_message_count(session, user_id)
