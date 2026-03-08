"""
APScheduler job definitions.
Phase 1 stub — scheduler is initialized but no jobs are registered yet.
Full implementation in Phase 3.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError

from config import settings

# Convert asyncpg URL to sync SQLAlchemy URL for APScheduler's job store
_sync_db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=_sync_db_url)},
    timezone="Asia/Seoul",
)


async def schedule_user_followup(user_id: str, delay_minutes: int, reason: str, session) -> None:
    """Schedule a one-time agent invocation for a user in delay_minutes minutes."""
    from db import queries as db

    await cancel_user_followup(user_id, session)

    job_id = f"followup_{user_id}"
    run_at = datetime.now().astimezone() + timedelta(minutes=delay_minutes)

    scheduler.add_job(
        _invoke_agent_job,
        trigger="date",
        run_date=run_at,
        id=job_id,
        args=[user_id, reason],
        replace_existing=True,
    )

    await db.upsert_scheduled_followup(session, user_id, job_id=job_id, reason=reason, scheduled_for=run_at)


async def cancel_user_followup(user_id: str, session) -> None:
    """Cancel any pending follow-up for the user."""
    from db import queries as db

    job_id = f"followup_{user_id}"
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass
    await db.delete_scheduled_followup(session, user_id)


async def _invoke_agent_job(user_id: str, reason: str) -> None:
    """Called by APScheduler. Invokes the Orchestrator for a specific user."""
    from agent.orchestrator import invoke_agent
    from db.models import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await invoke_agent(user_id=user_id, trigger_reason=reason, session=session)
