"""
Kakao Local API — gym search and geofencing helpers.
"""
from __future__ import annotations

import json

import httpx

from config import settings

KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
WORK_RADIUS_METERS = 200  # distance threshold to consider user "at work"
GYM_RADIUS_METERS = 300


async def search_nearby_gyms(lat: float, lng: float, radius: int = 1000) -> list[dict]:
    """Search for gyms near a lat/lng coordinate using Kakao Local API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            KAKAO_LOCAL_URL,
            headers={"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"},
            params={
                "query": "헬스장",
                "x": lng,
                "y": lat,
                "radius": radius,
                "sort": "distance",
            },
        )
        resp.raise_for_status()
        return resp.json().get("documents", [])


async def get_location_status(user_id: str, current_lat: float, current_lng: float, session) -> str:
    """
    Derive a high-level location label for the user.
    Returns one of: 'at_work', 'near_gym', 'commuting', 'unknown'
    """
    from db import queries as db
    from utils.haversine import haversine_meters

    user = await db.get_user(session, user_id)
    if user is None:
        return "unknown"

    # Check if user is at workplace
    if user.workplace_lat and user.workplace_lng:
        try:
            dist = haversine_meters(
                float(user.workplace_lat), float(user.workplace_lng),
                current_lat, current_lng,
            )
            if dist <= WORK_RADIUS_METERS:
                return "at_work"
        except (ValueError, TypeError):
            pass

    # Check if near a gym
    gyms = await search_nearby_gyms(current_lat, current_lng, radius=GYM_RADIUS_METERS)
    if gyms:
        return "near_gym"

    return "commuting"
