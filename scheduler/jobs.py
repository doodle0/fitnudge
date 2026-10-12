"""
APScheduler setup and one-shot follow-up jobs.

Jobs persist in PostgreSQL (SQLAlchemyJobStore) so follow-ups survive restarts. The job
function is referenced by import path, so keep `_invoke_agent_job` where it is.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from utils.time_utils import KST, is_quiet_hour, now_kst, parse_local_time_today

log = logging.getLogger(__name__)

# APScheduler's job store needs a sync driver (psycopg2)
_sync_db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=_sync_db_url)},
    timezone="Asia/Seoul",
)

MIN_LEAD = timedelta(minutes=5)
MAX_LEAD = timedelta(hours=24)


def _job_id(user_id: str) -> str:
    return f"followup_{user_id}"


def resolve_followup_time(
    at: str | None = None, in_minutes: int | None = None, now: datetime | None = None
) -> datetime:
    """Turn the agent's `at` / `in_minutes` into an aware KST datetime, or raise ValueError."""
    now = (now or now_kst()).astimezone(KST)
    if (at is None) == (in_minutes is None):
        raise ValueError("Give exactly one of `at` (HH:MM) or `in_minutes`.")
    if at is not None:
        try:
            run_at = parse_local_time_today(at, now=now)
        except ValueError:
            raise ValueError(f"Invalid time '{at}'. Use HH:MM (24h, KST).") from None
    else:
        run_at = now + timedelta(minutes=int(in_minutes))
    if run_at - now < MIN_LEAD:
        raise ValueError("Follow-up must be at least 5 minutes from now.")
    if run_at - now > MAX_LEAD:
        raise ValueError("Follow-up must be within 24 hours.")
    if is_quiet_hour(run_at, settings.quiet_start, settings.quiet_end):
        raise ValueError(
            f"{run_at:%H:%M} is inside quiet hours ({settings.quiet_start:02d}:00–{settings.quiet_end:02d}:00); "
            "proactive messages would be blocked. Pick another time or don't schedule."
        )
    return run_at


async def schedule_user_followup(user_id: str, run_at: datetime, reason: str, session) -> None:
    """Schedule the single pending follow-up for a user (replaces any existing one)."""
    from db import queries as db

    job_id = _job_id(user_id)
    scheduler.add_job(
        _invoke_agent_job,
        trigger="date",
        run_date=run_at,
        id=job_id,
        args=[user_id, reason],
        replace_existing=True,
        misfire_grace_time=15 * 60,
    )
    await db.upsert_scheduled_followup(session, user_id, job_id=job_id, reason=reason, scheduled_for=run_at)
    log.info("Follow-up scheduled | user=%s | at=%s", user_id, run_at.isoformat())


async def cancel_user_followup(user_id: str, session) -> None:
    """Cancel any pending follow-up for the user."""
    from db import queries as db

    try:
        scheduler.remove_job(_job_id(user_id))
    except JobLookupError:
        pass
    await db.delete_scheduled_followup(session, user_id)


async def _invoke_agent_job(user_id: str, reason: str) -> None:
    """Called by APScheduler when a follow-up is due."""
    from agent import dispatch
    from agent.triggers import Trigger
    from db import queries as db
    from db.models import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await db.delete_scheduled_followup(session, user_id)  # it's firing now; no longer pending
    task = dispatch.submit(user_id, Trigger.followup(reason))
    if task is not None:
        await task
