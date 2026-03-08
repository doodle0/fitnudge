"""
Kakao OAuth routes: /auth/kakao and /auth/callback
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import get_db
from kakao.auth import (
    exchange_code_for_tokens,
    get_authorization_url,
    get_kakao_user_info,
    token_expires_at_from_response,
)
from db import queries as db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/kakao")
async def kakao_login():
    """Redirect the user to Kakao's OAuth authorization page."""
    url = get_authorization_url()
    return RedirectResponse(url)


@router.get("/callback")
async def kakao_callback(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    Handle the OAuth callback from Kakao.
    Exchange the authorization code for tokens, fetch user info, and upsert the user in DB.
    """
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error or not code:
        raise HTTPException(status_code=400, detail=f"Kakao OAuth error: {error or 'missing code'}")

    try:
        tokens = await exchange_code_for_tokens(code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    expires_at = token_expires_at_from_response(tokens)

    try:
        user_info = await get_kakao_user_info(access_token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Kakao user info: {exc}") from exc

    kakao_id: int = user_info["id"]
    kakao_nickname: str = (
        user_info.get("kakao_account", {})
        .get("profile", {})
        .get("nickname", "")
    )

    user = await db.upsert_user_from_kakao(
        session,
        kakao_id=kakao_id,
        kakao_nickname=kakao_nickname,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
    )

    # In production, set a session cookie or JWT here.
    # For Phase 1, return user info as JSON for verification.
    return JSONResponse({
        "user_id": str(user.id),
        "kakao_id": user.kakao_id,
        "nickname": user.kakao_nickname,
        "onboarding_complete": user.onboarding_complete,
        "message": "Kakao login successful",
    })


@router.delete("/kakao/unlink")
async def delete_account(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    GDPR/PIPA-compliant full user data deletion.
    Expects `user_id` in the request body.
    """
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    user = await db.get_user(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await session.delete(user)
    await session.commit()
    return {"message": "Account and all associated data deleted."}
