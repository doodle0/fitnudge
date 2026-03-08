"""
Logic for deciding when the agent should be invoked for a given user.
Phase 1 stub — returns False always (no scheduler logic yet).
Full implementation in Phase 3.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User


async def should_invoke_for_user(user: User, session: AsyncSession) -> bool:
    """
    Return True if the Orchestrator should be woken for this user on this cron tick.

    Conditions (any one is sufficient):
    - User has left workplace (location-based)
    - Configured departure time reached (±10 min)
    - A scheduled follow-up is due (handled by APScheduler directly)
    - User has not worked out in 4+ days and it is a weekday evening
    - It is 21:00 on Sunday (weekly summary — handled by a dedicated cron job)

    Guard conditions (all must pass for invocation to proceed):
    - User has not already recorded a workout today
    - Current time is before silent_after_hour
    - User has not received max_daily_messages today
    - User has not declared a rest period in notes
    """
    # Phase 1: no scheduler logic — always False
    # Phase 3 will implement the full condition tree here.
    return False
