"""
KakaoTalk send-to-me message API.
"""
from __future__ import annotations

import json

import httpx

from config import settings

SEND_MESSAGE_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


async def send_message(user_id: str, text: str, session) -> None:
    """
    Send a plain-text KakaoTalk message to the user's own KakaoTalk (send-to-me).
    Automatically refreshes the token if a 401 is returned.
    `session` is an AsyncSession for token lookup.
    """
    from kakao.auth import get_valid_token

    token = await get_valid_token(user_id, session)
    template = json.dumps({
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": settings.app_base_url,
            "mobile_web_url": settings.app_base_url,
        },
    })

    async with httpx.AsyncClient() as client:
        resp = await _post_message(client, token, template)

        if resp.status_code == 401:
            # Token expired mid-flight — refresh once and retry
            token = await get_valid_token(user_id, session)
            resp = await _post_message(client, token, template)

        resp.raise_for_status()


async def _post_message(client: httpx.AsyncClient, token: str, template: str) -> httpx.Response:
    return await client.post(
        SEND_MESSAGE_URL,
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": template},
    )
