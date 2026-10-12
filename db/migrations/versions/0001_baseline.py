"""Baseline: Phase 1–2 schema as created by the old create_tables().

Existing dev databases created by create_tables() should be stamped instead of upgraded:
    uv run alembic stamp 0001_baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _user_fk() -> sa.ForeignKey:
    return sa.ForeignKey("users.id", ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kakao_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("kakao_nickname", sa.Text),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workplace_lat", sa.String),
        sa.Column("workplace_lng", sa.String),
        sa.Column("default_departure_time", sa.Time),
        sa.Column("weekly_goal_count", sa.Integer),
        sa.Column("preferred_exercises", postgresql.ARRAY(sa.Text)),
        sa.Column("timezone", sa.Text),
        sa.Column("onboarding_complete", sa.Boolean),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "workout_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), _user_fk(), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("muscle_groups", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("raw_user_message", sa.Text),
        sa.Column("agent_notes", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "agent_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), _user_fk(), nullable=False),
        sa.Column("date", sa.Date, nullable=False, server_default=sa.func.current_date()),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), _user_fk(), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "streaks",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), _user_fk(), primary_key=True),
        sa.Column("current_streak", sa.Integer),
        sa.Column("longest_streak", sa.Integer),
        sa.Column("last_workout_date", sa.Date),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "daily_message_counts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), _user_fk(), primary_key=True),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("count", sa.Integer),
    )
    op.create_table(
        "scheduled_followups",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), _user_fk(), primary_key=True),
        sa.Column("job_id", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in (
        "scheduled_followups", "daily_message_counts", "streaks",
        "conversation_turns", "agent_notes", "workout_sessions", "users",
    ):
        op.drop_table(table)
