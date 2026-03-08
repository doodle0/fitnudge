"""
Typed async query helpers for all database operations.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AgentNote,
    ConversationTurn,
    DailyMessageCount,
    ScheduledFollowup,
    Streak,
    User,
    WorkoutSession,
)


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
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
        )
        session.add(user)
        # Also create an empty streak row
        await session.flush()  # get user.id
        streak = Streak(user_id=user.id)
        session.add(streak)
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
# Workout session queries
# ---------------------------------------------------------------------------

async def get_workout_sessions(session: AsyncSession, user_id: str, days: int = 30) -> list[WorkoutSession]:
    since = date.today() - timedelta(days=days)
    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == uuid.UUID(user_id), WorkoutSession.date >= since)
        .order_by(WorkoutSession.date.desc())
    )
    return list(result.scalars().all())


async def get_workout_session_today(session: AsyncSession, user_id: str) -> Optional[WorkoutSession]:
    result = await session.execute(
        select(WorkoutSession).where(
            WorkoutSession.user_id == uuid.UUID(user_id),
            WorkoutSession.date == date.today(),
        )
    )
    return result.scalar_one_or_none()


async def insert_workout_session(
    session: AsyncSession,
    user_id: str,
    muscle_groups: list[str],
    raw_user_message: str = "",
    agent_notes_text: str = "",
    source: str = "user_report",
) -> WorkoutSession:
    ws = WorkoutSession(
        user_id=uuid.UUID(user_id),
        date=date.today(),
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

    today = date.today()
    yesterday = today - timedelta(days=1)

    if streak.last_workout_date == today:
        # Already recorded today — no change
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


# ---------------------------------------------------------------------------
# Agent notes queries
# ---------------------------------------------------------------------------

async def get_notes(session: AsyncSession, user_id: str, days: int = 14) -> list[AgentNote]:
    since = date.today() - timedelta(days=days)
    result = await session.execute(
        select(AgentNote)
        .where(AgentNote.user_id == uuid.UUID(user_id), AgentNote.date >= since)
        .order_by(AgentNote.date.desc(), AgentNote.created_at.desc())
    )
    return list(result.scalars().all())


async def insert_note(session: AsyncSession, user_id: str, note: str) -> AgentNote:
    an = AgentNote(user_id=uuid.UUID(user_id), date=date.today(), note=note)
    session.add(an)
    await session.commit()
    await session.refresh(an)
    return an


# ---------------------------------------------------------------------------
# Conversation turn queries
# ---------------------------------------------------------------------------

async def insert_conversation_turn(
    session: AsyncSession, user_id: str, role: str, content: str
) -> ConversationTurn:
    ct = ConversationTurn(user_id=uuid.UUID(user_id), role=role, content=content)
    session.add(ct)
    await session.commit()
    await session.refresh(ct)
    return ct


# ---------------------------------------------------------------------------
# Daily message count queries
# ---------------------------------------------------------------------------

async def get_daily_message_count(session: AsyncSession, user_id: str, day: date = None) -> int:
    day = day or date.today()
    result = await session.execute(
        select(DailyMessageCount).where(
            DailyMessageCount.user_id == uuid.UUID(user_id),
            DailyMessageCount.date == day,
        )
    )
    row = result.scalar_one_or_none()
    return row.count if row else 0


async def increment_daily_message_count(session: AsyncSession, user_id: str) -> None:
    day = date.today()
    result = await session.execute(
        select(DailyMessageCount).where(
            DailyMessageCount.user_id == uuid.UUID(user_id),
            DailyMessageCount.date == day,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.count += 1
    else:
        session.add(DailyMessageCount(user_id=uuid.UUID(user_id), date=day, count=1))
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
