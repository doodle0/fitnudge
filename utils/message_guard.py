"""
Hard messaging rules — enforced in code, independent of the LLM.

Reactive replies (the user wrote to us) are always allowed up to an abuse cap.
Proactive messages (we reach out first) must pass every rule below.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from utils.time_utils import KST, is_quiet_hour, now_kst


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


ALLOW = GuardDecision(True)


def _deny(reason: str) -> GuardDecision:
    return GuardDecision(False, f"Blocked by FitNudge policy: {reason} Do not retry; end this run.")


async def check_send(
    session: AsyncSession,
    user_id: str,
    *,
    proactive: bool,
    sent_this_run: int = 0,
    now: datetime | None = None,
) -> GuardDecision:
    """Decide whether one more message may be sent to the user right now."""
    from db import queries as db

    if sent_this_run >= settings.max_messages_per_run:
        return _deny(f"at most {settings.max_messages_per_run} messages per run.")

    proactive_count, reply_count = await db.get_daily_counts(session, user_id)

    if not proactive:
        if reply_count >= settings.max_daily_replies:
            return _deny("daily reply limit reached.")
        return ALLOW

    now = now or now_kst()
    if is_quiet_hour(now, settings.quiet_start, settings.quiet_end):
        return _deny(
            f"quiet hours ({settings.quiet_start:02d}:00–{settings.quiet_end:02d}:00) — "
            "no proactive messages."
        )

    user = await db.get_user(session, user_id)
    if user is None:
        return _deny("unknown user.")
    if user.proactive_snoozed_until is not None and now < user.proactive_snoozed_until:
        return _deny(f"proactive messages are snoozed until {user.proactive_snoozed_until.astimezone(KST):%m-%d %H:%M}.")
    identity = await db.get_identity_for_user(session, user_id, user.active_channel)
    if identity is None or not identity.opted_in_proactive:
        return _deny("the user has not opted in to proactive messages (or paused them).")

    if proactive_count >= settings.max_daily_proactive:
        return _deny(f"daily proactive limit ({settings.max_daily_proactive}) reached.")

    unanswered = await db.count_unanswered_proactive_today(session, user_id)
    if unanswered >= settings.max_unanswered_proactive:
        return _deny(f"{unanswered} proactive messages are unanswered today — stay silent until the user replies.")

    if await db.get_workout_session_today(session, user_id) is not None:
        return _deny("the user already worked out today — no nudging.")

    return ALLOW


async def record_send(session: AsyncSession, user_id: str, *, proactive: bool) -> None:
    from db import queries as db

    await db.increment_daily_count(session, user_id, proactive=proactive)
