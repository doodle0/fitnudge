"""Phase 4: proactive loop — rest days, snooze, follow-up kinds, timing profile.

Revision ID: 0003_proactive_loop
Revises: 0002_platform_neutral_memory
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_proactive_loop"
down_revision = "0002_platform_neutral_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("rest_weekdays", postgresql.ARRAY(sa.Integer), nullable=False,
                                     server_default=sa.text("'{}'")))
    op.add_column("users", sa.Column("proactive_snoozed_until", sa.DateTime(timezone=True)))

    op.add_column("scheduled_followups", sa.Column("kind", sa.Text, nullable=False, server_default="custom"))
    op.add_column("scheduled_followups", sa.Column("commitment_at", sa.DateTime(timezone=True)))

    op.create_table(
        "timing_profile",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("learned_minutes", postgresql.ARRAY(sa.Integer)),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_window_fired_on", sa.Date),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("timing_profile")
    op.drop_column("scheduled_followups", "commitment_at")
    op.drop_column("scheduled_followups", "kind")
    op.drop_column("users", "proactive_snoozed_until")
    op.drop_column("users", "rest_weekdays")
