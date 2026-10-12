import uuid

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Index,
    Integer, String, Text, Time, UniqueConstraint,
    func, text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncAttrs, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from config import settings


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    """Platform-neutral user. Channel-specific IDs live in ChannelIdentity."""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name = Column(Text)
    active_channel = Column(Text, nullable=False, server_default="discord")
    # Kakao Login columns — nullable; only used by the future Kakao expansion
    kakao_id = Column(BigInteger, unique=True, nullable=True)
    kakao_nickname = Column(Text)
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expires_at = Column(DateTime(timezone=True))
    workplace_lat = Column(String)  # stored as string for encryption compatibility
    workplace_lng = Column(String)
    default_departure_time = Column(Time)
    weekly_goal_count = Column(Integer, default=3)
    preferred_exercises = Column(ARRAY(Text))
    timezone = Column(Text, default="Asia/Seoul")
    onboarding_complete = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    channel_identities = relationship("ChannelIdentity", back_populates="user", cascade="all, delete-orphan")
    workout_sessions = relationship("WorkoutSession", back_populates="user", cascade="all, delete-orphan")
    memory_entries = relationship("MemoryEntry", back_populates="user", cascade="all, delete-orphan")
    conversation_turns = relationship("ConversationTurn", back_populates="user", cascade="all, delete-orphan")
    streak = relationship("Streak", back_populates="user", uselist=False, cascade="all, delete-orphan")
    daily_message_counts = relationship("DailyMessageCount", back_populates="user", cascade="all, delete-orphan")
    scheduled_followup = relationship("ScheduledFollowup", back_populates="user", uselist=False, cascade="all, delete-orphan")


class ChannelIdentity(Base):
    """Maps a user to their account on one messaging platform."""
    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_channel_identities_channel_external_id"),
        UniqueConstraint("user_id", "channel", name="uq_channel_identities_user_channel"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel = Column(Text, nullable=False)        # 'discord' | 'kakao'
    external_id = Column(Text, nullable=False)    # Discord user snowflake / Kakao botUserKey
    opted_in_proactive = Column(Boolean, nullable=False, server_default=sql_text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="channel_identities")


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    muscle_groups = Column(ARRAY(Text), nullable=False)
    raw_user_message = Column(Text)
    agent_notes = Column(Text)
    source = Column(Text)  # 'user_report', 'manual'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="workout_sessions")


class MemoryEntry(Base):
    """Time-keyed plaintext memory: one (timestamp → statement) record. Append-only."""
    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("idx_memory_entries_user_at", "user_id", sql_text("at DESC")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    kind = Column(Text, nullable=False)
    text = Column(Text, nullable=False)
    pinned = Column(Boolean, nullable=False, server_default=sql_text("false"))
    superseded_by = Column(UUID(as_uuid=True), ForeignKey("memory_entries.id", ondelete="SET NULL"))
    source = Column(Text, nullable=False, server_default="agent")  # 'agent' | 'extractor' | 'migration'
    source_turn_id = Column(UUID(as_uuid=True), ForeignKey("conversation_turns.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="memory_entries")


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False)  # 'agent' or 'user'
    content = Column(Text, nullable=False)
    proactive = Column(Boolean, nullable=False, server_default=sql_text("false"))  # agent-initiated message
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="conversation_turns")


class Streak(Base):
    __tablename__ = "streaks"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_workout_date = Column(Date)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="streak")


class DailyMessageCount(Base):
    __tablename__ = "daily_message_counts"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    date = Column(Date, primary_key=True)
    proactive_count = Column(Integer, nullable=False, server_default=sql_text("0"))
    reply_count = Column(Integer, nullable=False, server_default=sql_text("0"))

    user = relationship("User", back_populates="daily_message_counts")


class ScheduledFollowup(Base):
    __tablename__ = "scheduled_followups"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    job_id = Column(Text, nullable=False)
    reason = Column(Text)
    scheduled_for = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="scheduled_followup")


# Async engine and session factory
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
