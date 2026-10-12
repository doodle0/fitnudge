"""Phase 3: platform-neutral users, channel identities, time-keyed memory, split counters.

- users: Kakao Login columns become nullable; add display_name, active_channel
- channel_identities: new
- conversation_turns: add proactive flag
- memory_entries: new; backfilled from agent_notes (at = date 12:00 KST), then agent_notes dropped
- daily_message_counts: count -> proactive_count, add reply_count

Revision ID: 0002_platform_neutral_memory
Revises: 0001_baseline
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_platform_neutral_memory"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    for col in ("kakao_id", "access_token", "refresh_token", "token_expires_at"):
        op.alter_column("users", col, nullable=True)
    op.add_column("users", sa.Column("display_name", sa.Text))
    op.add_column("users", sa.Column("active_channel", sa.Text, nullable=False, server_default="discord"))
    op.execute(
        "UPDATE users SET display_name = kakao_nickname, active_channel = 'kakao' "
        "WHERE kakao_id IS NOT NULL"
    )

    # channel_identities
    op.create_table(
        "channel_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("opted_in_proactive", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("channel", "external_id", name="uq_channel_identities_channel_external_id"),
        sa.UniqueConstraint("user_id", "channel", name="uq_channel_identities_user_channel"),
    )

    # conversation_turns
    op.add_column(
        "conversation_turns",
        sa.Column("proactive", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    # memory_entries
    op.create_table(
        "memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("memory_entries.id", ondelete="SET NULL")),
        sa.Column("source", sa.Text, nullable=False, server_default="agent"),
        sa.Column("source_turn_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversation_turns.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_memory_entries_user_at", "memory_entries", ["user_id", sa.text("at DESC")])
    op.execute(
        """
        INSERT INTO memory_entries (id, user_id, at, kind, text, source, created_at)
        SELECT id, user_id,
               (date + TIME '12:00') AT TIME ZONE 'Asia/Seoul',
               'note', note, 'migration', created_at
        FROM agent_notes
        """
    )
    op.drop_table("agent_notes")

    # daily_message_counts
    op.alter_column("daily_message_counts", "count", new_column_name="proactive_count")
    op.execute("UPDATE daily_message_counts SET proactive_count = 0 WHERE proactive_count IS NULL")
    op.alter_column("daily_message_counts", "proactive_count",
                    nullable=False, server_default=sa.text("0"))
    op.add_column(
        "daily_message_counts",
        sa.Column("reply_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("daily_message_counts", "reply_count")
    op.alter_column("daily_message_counts", "proactive_count",
                    new_column_name="count", nullable=True, server_default=None)

    op.create_table(
        "agent_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date, nullable=False, server_default=sa.func.current_date()),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        """
        INSERT INTO agent_notes (id, user_id, date, note, created_at)
        SELECT id, user_id, (at AT TIME ZONE 'Asia/Seoul')::date, text, created_at
        FROM memory_entries
        """
    )
    op.drop_index("idx_memory_entries_user_at", table_name="memory_entries")
    op.drop_table("memory_entries")

    op.drop_column("conversation_turns", "proactive")
    op.drop_table("channel_identities")

    op.drop_column("users", "active_channel")
    op.drop_column("users", "display_name")
    # Rows without Kakao credentials (Discord users) cannot satisfy the old NOT NULLs.
    op.execute("DELETE FROM users WHERE kakao_id IS NULL")
    for col in ("kakao_id", "access_token", "refresh_token", "token_expires_at"):
        op.alter_column("users", col, nullable=False)
