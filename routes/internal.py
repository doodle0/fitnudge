"""
Internal routes used by the scheduler / cron system.
"""
from __future__ import annotations

import hmac
import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import get_db
from db import queries as db

router = APIRouter(prefix="/internal", tags=["internal"])


def _make_hmac(message: bytes) -> str:
    return hmac.new(
        settings.secret_key.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()


def _verify_internal_signature(x_internal_token: str = Header(default="")) -> None:
    """Simple HMAC guard so external callers cannot hit internal endpoints."""
    expected = _make_hmac(b"scheduler-tick")
    if not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def _verify_trigger_signature(x_internal_token: str = Header(default="")) -> None:
    """HMAC guard for the manual trigger endpoint."""
    expected = _make_hmac(b"trigger")
    if not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


class TriggerRequest(BaseModel):
    user_id: str
    trigger_reason: str = "manual_trigger"
    user_message: Optional[str] = None


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


@router.post("/trigger", dependencies=[Depends(_verify_trigger_signature)])
async def trigger_agent(
    body: TriggerRequest,
    session: AsyncSession = Depends(get_db),
):
    """
    Manually invoke the Orchestrator Agent for a specific user.
    Useful for testing and manual overrides.

    Authentication: set X-Internal-Token to HMAC-SHA256(SECRET_KEY, "trigger").
    """
    from agent.orchestrator import invoke_agent

    user = await db.get_user(session, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await invoke_agent(
        user_id=body.user_id,
        trigger_reason=body.trigger_reason,
        user_message=body.user_message,
        session=session,
    )
    return {"status": "triggered", "user_id": body.user_id, "trigger_reason": body.trigger_reason}
