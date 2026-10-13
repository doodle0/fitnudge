"""
Habit learning: when does this user usually work out? Runs nightly.

Signal: the time the user *reports* a workout (≈ when they finish). The nudge should land before
they would start, so the learned nudge time is the median report time minus REPORT_TO_NUDGE.
Workouts reported for another day ("어제 했어") carry no time-of-day signal and are skipped.
"""
from __future__ import annotations

import logging
import statistics

from db.models import WorkoutSession
from utils.time_utils import KST

log = logging.getLogger(__name__)

REPORT_TO_NUDGE_MINUTES = 120
EARLIEST_NUDGE = 8 * 60       # 08:00
LATEST_NUDGE = 21 * 60        # 21:00
MIN_SAMPLES_PER_WEEKDAY = 2
MIN_SAMPLES_PER_DAY_TYPE = 2  # weekday (Mon–Fri) vs weekend fallback


def learn_minutes(sessions: list[WorkoutSession]) -> tuple[list[int | None], int]:
    """Return (7 nudge minutes-of-day Mon…Sun, or None where unknown; number of usable samples)."""
    by_weekday: dict[int, list[int]] = {d: [] for d in range(7)}
    for s in sessions:
        reported = s.created_at.astimezone(KST)
        if reported.date() != s.date:
            continue
        minute = reported.hour * 60 + reported.minute - REPORT_TO_NUDGE_MINUTES
        by_weekday[s.date.weekday()].append(min(max(minute, EARLIEST_NUDGE), LATEST_NUDGE))

    samples = sum(len(v) for v in by_weekday.values())
    day_type_median: dict[bool, int | None] = {}
    for is_weekend in (False, True):
        pool = [m for d, ms in by_weekday.items() if (d >= 5) == is_weekend for m in ms]
        day_type_median[is_weekend] = (
            int(statistics.median(pool)) if len(pool) >= MIN_SAMPLES_PER_DAY_TYPE else None
        )

    learned: list[int | None] = []
    for d in range(7):
        if len(by_weekday[d]) >= MIN_SAMPLES_PER_WEEKDAY:
            learned.append(int(statistics.median(by_weekday[d])))
        else:
            learned.append(day_type_median[d >= 5])
    return learned, samples


async def refresh_profile(session, user_id: str) -> list[int | None]:
    from db import queries as db

    learned, samples = learn_minutes(await db.get_user_report_sessions(session, user_id))
    await db.upsert_timing_profile(session, user_id, learned, samples)
    return learned


async def refresh_all() -> int:
    """Nightly job: recompute every proactive user's timing profile."""
    from db import queries as db
    from db.models import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        users = await db.get_proactive_candidates(session)
        for user in users:
            try:
                await refresh_profile(session, str(user.id))
            except Exception:  # noqa: BLE001 — one bad user must not stop the rest
                log.exception("Timing refresh failed | user=%s", user.id)
                await session.rollback()
    log.info("Timing profiles refreshed | users=%d", len(users))
    return len(users)
