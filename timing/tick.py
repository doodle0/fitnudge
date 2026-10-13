"""
The 5-minute tick: open each user's daily nudge window at most once per day.
"""
from __future__ import annotations

import logging
from datetime import datetime

from utils.time_utils import now_kst

log = logging.getLogger(__name__)


async def run_tick(now: datetime | None = None) -> list[str]:
    """Submit a proactive run for every user whose window is open. Returns their ids."""
    from agent import dispatch
    from agent.triggers import Trigger
    from db import queries as db
    from db.models import AsyncSessionLocal
    from timing.windows import check_window

    now = now or now_kst()
    fired: list[str] = []
    async with AsyncSessionLocal() as session:
        for user in await db.get_proactive_candidates(session):
            user_id = str(user.id)
            try:
                is_open, reason, _window = await check_window(session, user, now)
                if not is_open:
                    continue
                if not await db.mark_window_fired(session, user_id, now.date()):
                    continue  # another tick claimed it
            except Exception:  # noqa: BLE001
                log.exception("Window check failed | user=%s", user_id)
                await session.rollback()
                continue
            dispatch.submit(user_id, Trigger.scheduled(reason))
            fired.append(user_id)
            log.info("Daily window opened | user=%s | %s", user_id, reason)
    return fired
