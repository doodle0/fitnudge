"""
Kakao Calendar API — read today's events to detect overtime / dinner plans.
"""
from __future__ import annotations

from datetime import date

import httpx

KAKAO_CALENDAR_URL = "https://kapi.kakao.com/v2/api/calendar/events"


async def get_calendar_events(user_id: str, target_date: str = "today", session=None) -> list[dict]:
    """
    Fetch Kakao Calendar events for the user on a given date.
    target_date: 'today' or an ISO date string (YYYY-MM-DD).
    Returns a list of event dicts.
    """
    from kakao.auth import get_valid_token

    if session is None:
        return []

    token = await get_valid_token(user_id, session)

    if target_date == "today":
        day_str = date.today().isoformat()
    else:
        day_str = target_date

    # Kakao Calendar API expects RFC3339 datetime range
    from_dt = f"{day_str}T00:00:00+09:00"
    to_dt = f"{day_str}T23:59:59+09:00"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            KAKAO_CALENDAR_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"from": from_dt, "to": to_dt},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", [])


def format_calendar_events(events: list[dict]) -> str:
    """Format calendar events into a readable string for the agent."""
    if not events:
        return "No calendar events today."
    lines = []
    for ev in events:
        title = ev.get("title", "(no title)")
        start = ev.get("start_at", "")
        lines.append(f"- {title} ({start})")
    return "\n".join(lines)
