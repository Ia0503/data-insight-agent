"""Persist fixed-hour token budgets and password attempt limits."""

import sqlalchemy as sa
from alembic import op

revision = "0004_call_protection"
down_revision = "0003_agent"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "token_hours",
        sa.Column("hour_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unknown_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "used_tokens >= 0 AND reserved_tokens >= 0", name="token_hour_nonnegative"
        ),
    )
    op.create_table(
        "token_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hour_start",
            sa.DateTime(timezone=True),
            sa.ForeignKey("token_hours.hour_start"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("charged_tokens", sa.BigInteger()),
        sa.Column("usage_known", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "amount > 0 AND (charged_tokens IS NULL OR charged_tokens >= 0)",
            name="token_reservation_nonnegative",
        ),
        sa.CheckConstraint("state IN ('reserved','settled')", name="token_reservation_state"),
    )
    op.create_index("ix_token_reservations_hour_start", "token_reservations", ["hour_start"])
    op.create_index("ix_token_reservations_run_id", "token_reservations", ["run_id"])
    op.create_table(
        "access_attempts",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "window_start", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("access_attempts")
    op.drop_table("token_reservations")
    op.drop_table("token_hours")
