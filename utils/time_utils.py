"""
Korean timezone helpers.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """Return the current datetime in KST."""
    return datetime.now(tz=KST)


def current_hour_kst() -> int:
    """Return the current hour (0–23) in KST."""
    return now_kst().hour


def is_silent_hour(silent_after: int = 22) -> bool:
    """Return True if the current KST hour is past the silent threshold."""
    return current_hour_kst() >= silent_after


def minutes_until(target_hour: int, target_minute: int = 0) -> int:
    """
    Return the number of minutes from now (KST) until the given KST time today.
    If the time has already passed, returns 0.
    """
    now = now_kst()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    delta = (target - now).total_seconds()
    return max(0, int(delta // 60))
