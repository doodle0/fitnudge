"""
Kakao OAuth 2.0 — token exchange, refresh, and validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from config import settings

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USER_URL = "https://kapi.kakao.com/v2/user/me"
KAKAO_AUTH_BASE = "https://kauth.kakao.com/oauth/authorize"


def get_authorization_url(state: str = "") -> str:
    """Build the Kakao OAuth authorization URL to redirect the user to."""
    params = (
        f"?client_id={settings.kakao_rest_api_key}"
        f"&redirect_uri={settings.kakao_redirect_uri}"
        f"&response_type=code"
        f"&scope=talk_message"
    )
    if state:
        params += f"&state={state}"
    return KAKAO_AUTH_BASE + params


async def exchange_code_for_tokens(code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            KAKAO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.kakao_rest_api_key,
                "client_secret": settings.kakao_client_secret,
                "redirect_uri": settings.kakao_redirect_uri,
                "code": code,
            },
        )
        resp.raise_for_status()
        return resp.json()  # access_token, refresh_token, expires_in


async def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a new access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            KAKAO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.kakao_rest_api_key,
                "client_secret": settings.kakao_client_secret,
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()  # access_token, (optionally new refresh_token), expires_in


async def get_kakao_user_info(access_token: str) -> dict:
    """Fetch the Kakao user profile using a valid access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            KAKAO_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def token_expires_at_from_response(tokens: dict) -> datetime:
    """Calculate token expiry datetime from the Kakao token response."""
    expires_in: int = tokens.get("expires_in", 21599)  # default ~6h
    return datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)


async def get_valid_token(user_id: str, session) -> str:
    """
    Return a valid access token for the user, refreshing if needed.
    `session` is an AsyncSession dependency from FastAPI.
    """
    from db import queries as db

    user = await db.get_user(session, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    # Refresh 5 minutes early to avoid edge-case expiry
    if user.token_expires_at < datetime.now(tz=timezone.utc) + timedelta(minutes=5):
        tokens = await refresh_access_token(user.refresh_token)
        new_access = tokens["access_token"]
        # Kakao only returns a new refresh_token if the old one is near expiry
        new_refresh = tokens.get("refresh_token", user.refresh_token)
        expires_at = token_expires_at_from_response(tokens)
        await db.update_user_tokens(session, user_id, new_access, new_refresh, expires_at)
        return new_access

    return user.access_token
