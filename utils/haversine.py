"""
Haversine formula — great-circle distance between two lat/lng coordinates.
"""
from __future__ import annotations

import math


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Return the distance in meters between two GPS coordinates.
    Uses the haversine formula with Earth radius = 6,371,000 m.
    """
    R = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
