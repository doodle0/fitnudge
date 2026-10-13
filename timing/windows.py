"""
Daily nudge window: code decides *when* a proactive nudge is allowed; the agent decides *whether*
to send one and what to say.

Window start, in priority order:
  1. learned  — the user's habit, from timing_profile (see timing/learn.py)
  2. usual    — users.default_departure_time (the user told us when they're usually free)
  3. default  — 18:30 on weekdays, 11:00 on weekends
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TimingProfile, User
from utils.time_utils import KST

DEFAULT_WEEKDAY = time(18, 30)
DEFAULT_WEEKEND = time(11, 0)
WINDOW_LENGTH = timedelta(minutes=60)
RECENT_CHAT = timedelta(minutes=90)   # the user is mid-conversation: reactive runs handle it

WEEKDAYS_KO = "월화수목금토일"


@dataclass(frozen=True)
class DailyWindow:
    start: datetime
    end: datetime
    source: str  # learned | usual | default

    def contains(self, now: datetime) -> bool:
        return self.start <= now < self.end

    def describe(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M} ({self.source})"


def window_for(day: date, user: User, profile: TimingProfile | None) -> DailyWindow:
    weekday = day.weekday()
    learned = profile.learned_minutes if profile and profile.learned_minutes else None
    if learned and len(learned) == 7 and learned[weekday] is not None:
        start_time, source = time(learned[weekday] // 60, learned[weekday] % 60), "learned"
    elif user.default_departure_time is not None:
        start_time, source = user.default_departure_time, "usual"
    else:
        start_time, source = (DEFAULT_WEEKDAY if weekday < 5 else DEFAULT_WEEKEND), "default"
    start = datetime.combine(day, start_time, tzinfo=KST)
    return DailyWindow(start=start, end=start + WINDOW_LENGTH, source=source)


async def check_window(session: AsyncSession, user: User, now: datetime) -> tuple[bool, str, DailyWindow]:
    """
    Should today's daily nudge run fire right now? Returns (open, reason, window).
    Proactive guards (quiet hours, caps, opt-in, snooze, workout today) are also checked here so a
    blocked user never costs a model call.
    """
    from db import queries as db
    from utils import message_guard

    now = now.astimezone(KST)
    user_id = str(user.id)
    profile = await db.get_timing_profile(session, user_id)
    window = window_for(now.date(), user, profile)

    if now.weekday() in (user.rest_weekdays or []):
        return False, f"rest day ({WEEKDAYS_KO[now.weekday()]})", window
    if not window.contains(now):
        return False, f"outside window {window.describe()}", window
    if profile is not None and profile.last_window_fired_on == now.date():
        return False, "window already fired today", window
    if await db.get_scheduled_followup(session, user_id) is not None:
        return False, "a follow-up is already pending", window
    proactive_count, _ = await db.get_daily_counts(session, user_id)
    if proactive_count > 0:
        return False, "already reached out today", window
    last_user = await db.get_last_turn_at(session, user_id, "user")
    if last_user is not None and now - last_user < RECENT_CHAT:
        return False, "user is mid-conversation", window

    decision = await message_guard.check_send(session, user_id, proactive=True, now=now)
    if not decision.allowed:
        return False, decision.reason, window
    return True, f"daily nudge window {window.describe()}", window
