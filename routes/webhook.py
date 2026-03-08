"""
Kakao webhook routes: incoming messages and location events.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import get_db
from db import queries as db

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/message")
async def kakao_message_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    """
    Receives incoming KakaoTalk messages from users via the Kakao chatbot webhook.
    Must respond with 200 OK immediately; agent is processed in the background.
    """
    payload = await request.json()

    user_kakao_id_str: str = payload.get("userRequest", {}).get("user", {}).get("id", "")
    user_message: str = payload.get("userRequest", {}).get("utterance", "")

    # Kakao sends the user's UUID string as the user ID in chatbot webhooks
    # We look up our internal user by their kakao_id
    # Note: chatbot user IDs are different from Kakao Login IDs;
    # proper linking requires onboarding flow. For now, try numeric lookup.
    try:
        kakao_id = int(user_kakao_id_str)
        user = await db.get_user_by_kakao_id(session, kakao_id)
    except (ValueError, TypeError):
        user = None

    if not user:
        # Unknown user — silently accept and return empty response
        return {"version": "2.0", "template": {"outputs": []}}

    user_id = str(user.id)

    # Persist the raw incoming message immediately
    await db.insert_conversation_turn(session, user_id, role="user", content=user_message)

    # Invoke the agent asynchronously so we can return 200 right away
    background_tasks.add_task(
        _invoke_agent_background,
        user_id=user_id,
        trigger_reason="incoming_user_message",
        user_message=user_message,
    )

    # Kakao chatbot expects this empty response shape
    return {"version": "2.0", "template": {"outputs": []}}


@router.post("/location")
async def kakao_location_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    """
    Receives location updates (geofence exit events) from the client.
    Expected payload: { user_id, lat, lng, event: 'exit_workplace' }
    """
    payload = await request.json()
    user_id: str = payload.get("user_id", "")
    event: str = payload.get("event", "")

    if not user_id or event != "exit_workplace":
        return {"status": "ignored"}

    user = await db.get_user(session, user_id)
    if not user:
        return {"status": "unknown_user"}

    background_tasks.add_task(
        _invoke_agent_background,
        user_id=user_id,
        trigger_reason="commute_detected",
    )

    return {"status": "accepted"}


async def _invoke_agent_background(
    user_id: str,
    trigger_reason: str,
    user_message: str | None = None,
) -> None:
    """
    Background task wrapper for agent invocation.
    Imports are deferred to avoid circular dependencies at startup.
    """
    try:
        from agent.orchestrator import invoke_agent
        from db.models import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await invoke_agent(
                user_id=user_id,
                trigger_reason=trigger_reason,
                user_message=user_message,
                session=session,
            )
    except Exception as exc:  # noqa: BLE001
        # Never crash the webhook — just log
        import logging
        logging.getLogger(__name__).exception("Agent invocation failed for user %s: %s", user_id, exc)
