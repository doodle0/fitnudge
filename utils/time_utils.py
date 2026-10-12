"""
Korean timezone helpers.

All "today" logic uses the user's local date (default Asia/Seoul), never the server's.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """Return the current datetime in KST."""
    return datetime.now(tz=KST)


def today_kst() -> date:
    """Return today's date in KST."""
    return now_kst().date()


def start_of_day_kst(day: date | None = None) -> datetime:
    """Return 00:00 KST of the given day (default: today) as an aware datetime."""
    return datetime.combine(day or today_kst(), time.min, tzinfo=KST)


def is_quiet_hour(now: datetime, quiet_start: int, quiet_end: int) -> bool:
    """
    True if `now` falls in the quiet window [quiet_start, quiet_end) local hours.
    Handles windows that wrap past midnight (e.g. 22 → 8).
    """
    hour = now.astimezone(KST).hour
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= hour < quiet_end
    return hour >= quiet_start or hour < quiet_end


def parse_local_time_today(hhmm: str, now: datetime | None = None) -> datetime:
    """
    Parse "HH:MM" as a KST time today. If that time has already passed, returns tomorrow's.
    Raises ValueError on malformed input.
    """
    now = (now or now_kst()).astimezone(KST)
    hour_str, minute_str = hhmm.strip().split(":")
    target = now.replace(hour=int(hour_str), minute=int(minute_str), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def minutes_until(target_hour: int, target_minute: int = 0) -> int:
    """
    Return the number of minutes from now (KST) until the given KST time today.
    If the time has already passed, returns 0.
    """
    now = now_kst()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    delta = (target - now).total_seconds()
    return max(0, int(delta // 60))
