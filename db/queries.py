"""
Typed async query helpers for all database operations.
Memory-entry queries live in memory/store.py.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ChannelIdentity,
    ConversationTurn,
    DailyMessageCount,
    ScheduledFollowup,
    Streak,
    User,
    WorkoutSession,
)
from utils.time_utils import start_of_day_kst, today_kst


# ---------------------------------------------------------------------------
# User queries
# ---------------------------------------------------------------------------

async def get_user(session: AsyncSession, user_id: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    return result.scalar_one_or_none()


async def get_user_by_kakao_id(session: AsyncSession, kakao_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.kakao_id == kakao_id))
    return result.scalar_one_or_none()


async def get_active_users(session: AsyncSession) -> list[User]:
    """Return all users with onboarding complete."""
    result = await session.execute(select(User).where(User.onboarding_complete.is_(True)))
    return list(result.scalars().all())


async def upsert_user_from_kakao(
    session: AsyncSession,
    kakao_id: int,
    kakao_nickname: str,
    access_token: str,
    refresh_token: str,
    token_expires_at: datetime,
) -> User:
    user = await get_user_by_kakao_id(session, kakao_id)
    if user:
        user.kakao_nickname = kakao_nickname
        user.access_token = access_token
        user.refresh_token = refresh_token
        user.token_expires_at = token_expires_at
        user.updated_at = datetime.now(tz=timezone.utc)
    else:
        user = User(
            kakao_id=kakao_id,
            kakao_nickname=kakao_nickname,
            display_name=kakao_nickname,
            active_channel="kakao",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
        )
        session.add(user)
        await session.flush()  # get user.id
        session.add(Streak(user_id=user.id))
    await session.commit()
    await session.refresh(user)
    return user


async def update_user_tokens(
    session: AsyncSession,
    user_id: str,
    access_token: str,
    refresh_token: str,
    token_expires_at: datetime,
) -> None:
    await session.execute(
        update(User)
        .where(User.id == uuid.UUID(user_id))
        .values(
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            updated_at=datetime.now(tz=timezone.utc),
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Channel identity queries
# ---------------------------------------------------------------------------

async def get_identity(session: AsyncSession, channel: str, external_id: str) -> Optional[ChannelIdentity]:
    result = await session.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def get_identity_for_user(session: AsyncSession, user_id: str, channel: str) -> Optional[ChannelIdentity]:
    result = await session.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.user_id == uuid.UUID(user_id),
            ChannelIdentity.channel == channel,
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_user_for_identity(
    session: AsyncSession,
    channel: str,
    external_id: str,
    display_name: str | None = None,
) -> tuple[User, ChannelIdentity, bool]:
    """Return (user, identity, created). Creates a user + streak row on first contact."""
    identity = await get_identity(session, channel, external_id)
    if identity is not None:
        user = await session.get(User, identity.user_id)
        return user, identity, False

    user = User(display_name=display_name, active_channel=channel)
    session.add(user)
    await session.flush()
    identity = ChannelIdentity(user_id=user.id, channel=channel, external_id=external_id)
    session.add_all([identity, Streak(user_id=user.id)])
    await session.commit()
    await session.refresh(user)
    await session.refresh(identity)
    return user, identity, True


async def set_opted_in_proactive(session: AsyncSession, user_id: str, channel: str, opted_in: bool) -> None:
    await session.execute(
        update(ChannelIdentity)
        .where(ChannelIdentity.user_id == uuid.UUID(user_id), ChannelIdentity.channel == channel)
        .values(opted_in_proactive=opted_in)
    )
    await session.commit()


async def mark_onboarding_complete(session: AsyncSession, user_id: str) -> None:
    await session.execute(
        update(User).where(User.id == uuid.UUID(user_id)).values(onboarding_complete=True)
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Workout session queries
# ---------------------------------------------------------------------------

async def get_workout_sessions(session: AsyncSession, user_id: str, days: int = 30) -> list[WorkoutSession]:
    since = today_kst() - timedelta(days=days)
    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == uuid.UUID(user_id), WorkoutSession.date >= since)
        .order_by(WorkoutSession.date.desc(), WorkoutSession.created_at.desc())
    )
    return list(result.scalars().all())


async def get_workout_session_today(session: AsyncSession, user_id: str) -> Optional[WorkoutSession]:
    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == uuid.UUID(user_id), WorkoutSession.date == today_kst())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def insert_workout_session(
    session: AsyncSession,
    user_id: str,
    muscle_groups: list[str],
    raw_user_message: str = "",
    agent_notes_text: str = "",
    source: str = "user_report",
    day: date | None = None,
) -> WorkoutSession:
    ws = WorkoutSession(
        user_id=uuid.UUID(user_id),
        date=day or today_kst(),
        muscle_groups=muscle_groups,
        raw_user_message=raw_user_message,
        agent_notes=agent_notes_text,
        source=source,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# Streak queries
# ---------------------------------------------------------------------------

async def get_streak(session: AsyncSession, user_id: str) -> Optional[Streak]:
    result = await session.execute(select(Streak).where(Streak.user_id == uuid.UUID(user_id)))
    return result.scalar_one_or_none()


async def update_streak(session: AsyncSession, user_id: str) -> Streak:
    """Recalculate and persist the streak for a user after a new workout is recorded."""
    streak = await get_streak(session, user_id)
    if streak is None:
        streak = Streak(user_id=uuid.UUID(user_id))
        session.add(streak)

    today = today_kst()
    yesterday = today - timedelta(days=1)

    if streak.last_workout_date == today:
        return streak

    if streak.last_workout_date == yesterday:
        streak.current_streak = (streak.current_streak or 0) + 1
    else:
        streak.current_streak = 1

    streak.last_workout_date = today
    if streak.current_streak > (streak.longest_streak or 0):
        streak.longest_streak = streak.current_streak

    await session.commit()
    await session.refresh(streak)
    return streak


async def count_workouts_this_week(session: AsyncSession, user_id: str) -> int:
    """Sessions since Monday (KST)."""
    today = today_kst()
    monday = today - timedelta(days=today.weekday())
    result = await session.execute(
        select(func.count(func.distinct(WorkoutSession.date))).where(
            WorkoutSession.user_id == uuid.UUID(user_id),
            WorkoutSession.date >= monday,
        )
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Conversation turn queries
# ---------------------------------------------------------------------------

async def insert_conversation_turn(
    session: AsyncSession, user_id: str, role: str, content: str, proactive: bool = False
) -> ConversationTurn:
    ct = ConversationTurn(user_id=uuid.UUID(user_id), role=role, content=content, proactive=proactive)
    session.add(ct)
    await session.commit()
    await session.refresh(ct)
    return ct


async def get_recent_turns(session: AsyncSession, user_id: str, limit: int = 20) -> list[ConversationTurn]:
    """Most recent turns, returned oldest-first."""
    result = await session.execute(
        select(ConversationTurn)
        .where(ConversationTurn.user_id == uuid.UUID(user_id))
        .order_by(ConversationTurn.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def count_unanswered_proactive_today(session: AsyncSession, user_id: str) -> int:
    """Proactive agent turns today that came after the user's most recent message."""
    uid = uuid.UUID(user_id)
    last_user_at = (
        await session.execute(
            select(func.max(ConversationTurn.created_at)).where(
                ConversationTurn.user_id == uid, ConversationTurn.role == "user"
            )
        )
    ).scalar_one()
    since = start_of_day_kst()
    if last_user_at is not None and last_user_at > since:
        since = last_user_at
    result = await session.execute(
        select(func.count()).where(
            ConversationTurn.user_id == uid,
            ConversationTurn.role == "agent",
            ConversationTurn.proactive.is_(True),
            ConversationTurn.created_at > since,
        )
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Daily message count queries
# ---------------------------------------------------------------------------

async def get_daily_counts(session: AsyncSession, user_id: str, day: date | None = None) -> tuple[int, int]:
    """Return (proactive_count, reply_count) for the user's local day."""
    result = await session.execute(
        select(DailyMessageCount).where(
            DailyMessageCount.user_id == uuid.UUID(user_id),
            DailyMessageCount.date == (day or today_kst()),
        )
    )
    row = result.scalar_one_or_none()
    return (row.proactive_count, row.reply_count) if row else (0, 0)


async def increment_daily_count(session: AsyncSession, user_id: str, proactive: bool) -> None:
    """Atomic upsert so concurrent sends never lose a count."""
    column = "proactive_count" if proactive else "reply_count"
    stmt = pg_insert(DailyMessageCount).values(
        user_id=uuid.UUID(user_id), date=today_kst(), **{column: 1}
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "date"],
        set_={column: getattr(DailyMessageCount, column) + 1},
    )
    await session.execute(stmt)
    await session.commit()


# ---------------------------------------------------------------------------
# Scheduled follow-up queries
# ---------------------------------------------------------------------------

async def get_scheduled_followup(session: AsyncSession, user_id: str) -> Optional[ScheduledFollowup]:
    result = await session.execute(
        select(ScheduledFollowup).where(ScheduledFollowup.user_id == uuid.UUID(user_id))
    )
    return result.scalar_one_or_none()


async def upsert_scheduled_followup(
    session: AsyncSession,
    user_id: str,
    job_id: str,
    reason: str,
    scheduled_for: datetime,
) -> None:
    existing = await get_scheduled_followup(session, user_id)
    if existing:
        existing.job_id = job_id
        existing.reason = reason
        existing.scheduled_for = scheduled_for
    else:
        session.add(
            ScheduledFollowup(
                user_id=uuid.UUID(user_id),
                job_id=job_id,
                reason=reason,
                scheduled_for=scheduled_for,
            )
        )
    await session.commit()


async def delete_scheduled_followup(session: AsyncSession, user_id: str) -> None:
    await session.execute(
        delete(ScheduledFollowup).where(ScheduledFollowup.user_id == uuid.UUID(user_id))
    )
    await session.commit()
