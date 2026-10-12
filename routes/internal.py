"""
Internal routes used by the scheduler / cron system and for manual testing.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import queries as db
from db.models import get_db

router = APIRouter(prefix="/internal", tags=["internal"])


def _make_hmac(message: bytes) -> str:
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def _verify_internal_signature(x_internal_token: str = Header(default="")) -> None:
    """Simple HMAC guard so external callers cannot hit internal endpoints."""
    if not hmac.compare_digest(x_internal_token, _make_hmac(b"scheduler-tick")):
        raise HTTPException(status_code=403, detail="Forbidden")


def _verify_trigger_signature(x_internal_token: str = Header(default="")) -> None:
    """HMAC guard for the manual trigger endpoint."""
    if not hmac.compare_digest(x_internal_token, _make_hmac(b"trigger")):
        raise HTTPException(status_code=403, detail="Forbidden")


class TriggerRequest(BaseModel):
    user_id: str
    trigger_reason: str = "manual_trigger"
    user_message: Optional[str] = None  # if set, simulate an inbound message from the user


@router.post("/scheduler-tick", dependencies=[Depends(_verify_internal_signature)])
async def scheduler_tick(session: AsyncSession = Depends(get_db)):
    """
    Called by an external cron every 5 minutes as a fallback to the in-process scheduler.
    Phase 4 implements should_invoke_for_user; until then this is a no-op.
    """
    from agent import dispatch
    from agent.triggers import Trigger
    from scheduler.triggers import should_invoke_for_user

    triggered = []
    for user in await db.get_active_users(session):
        if await should_invoke_for_user(user, session):
            dispatch.submit(str(user.id), Trigger(kind="scheduled", proactive=True, reason="scheduled_check"))
            triggered.append(str(user.id))
    return {"triggered_for": triggered}


@router.post("/trigger", dependencies=[Depends(_verify_trigger_signature)])
async def trigger_agent(body: TriggerRequest, session: AsyncSession = Depends(get_db)):
    """
    Manually wake the agent for a user and wait for the run to finish.
    Without `user_message` this is a proactive run (all proactive guards apply).

    Authentication: X-Internal-Token = HMAC-SHA256(SECRET_KEY, "trigger").
    """
    from agent import dispatch
    from agent.triggers import Trigger
    from channels.base import InboundMessage

    user = await db.get_user(session, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.user_message:
        identity = await db.get_identity_for_user(session, body.user_id, user.active_channel)
        if identity is None:
            raise HTTPException(status_code=409, detail="User has no identity on their active channel")
        task = await dispatch.handle_inbound(
            InboundMessage(channel=identity.channel, external_user_id=identity.external_id, text=body.user_message)
        )
    else:
        task = dispatch.submit(body.user_id, Trigger.manual(body.trigger_reason))

    if task is not None:
        await task
    return {"status": "done" if task else "queued", "user_id": body.user_id}
