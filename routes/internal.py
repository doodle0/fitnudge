"""
Internal routes used by the scheduler / cron system.
"""
from __future__ import annotations

import hmac
import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import get_db
from db import queries as db

router = APIRouter(prefix="/internal", tags=["internal"])


def _verify_internal_signature(x_internal_token: str = Header(default="")) -> None:
    """Simple HMAC guard so external callers cannot hit internal endpoints."""
    expected = hmac.new(
        settings.secret_key.encode(),
        b"scheduler-tick",
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/scheduler-tick", dependencies=[Depends(_verify_internal_signature)])
async def scheduler_tick(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    Called by an external cron (e.g., Railway cron, GitHub Actions) every 5 minutes.
    Runs the same logic as the in-process APScheduler check_all_users job,
    useful as a fallback if APScheduler is not running.
    """
    from scheduler.triggers import should_invoke_for_user
    from agent.orchestrator import invoke_agent

    users = await db.get_active_users(session)
    triggered = []
    for user in users:
        if await should_invoke_for_user(user, session):
            await invoke_agent(
                user_id=str(user.id),
                trigger_reason="scheduled_check",
                session=session,
            )
            triggered.append(str(user.id))

    return {"triggered_for": triggered}
