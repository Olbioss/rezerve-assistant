"""Conversations, messages, pending approvals and run metrics.

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_slug", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("channel_user_id", sa.Text(), nullable=False),
        sa.Column("customer_ref", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="active", nullable=False),
        sa.Column("history", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "channel IN ('web', 'telegram', 'whatsapp')", name="conversations_channel"
        ),
        sa.CheckConstraint("state IN ('active', 'human', 'closed')", name="conversations_state"),
    )
    op.create_index(
        "conversations_open_per_user",
        "conversations",
        ["business_slug", "channel", "channel_user_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'closed'"),
    )
    op.create_index("conversations_last_message_at", "conversations", ["last_message_at"])

    op.create_table(
        "messages",
        sa.Column("seq", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("channel_message_id", sa.Text(), nullable=True),
        sa.Column("sent_message_id", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("actions", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("direction IN ('in', 'out', 'owner')", name="messages_direction"),
    )
    op.create_index(
        "messages_inbound_once",
        "messages",
        ["channel", "channel_message_id"],
        unique=True,
        postgresql_where=sa.text("direction = 'in'"),
    )
    op.create_index("messages_conversation_seq", "messages", ["conversation_id", "seq"])

    op.create_table(
        "pending_approvals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("args", JSONB(), nullable=False),
        sa.Column(
            "other_call_ids", ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("history", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "decision IN ('approved', 'denied', 'expired')", name="pending_approvals_decision"
        ),
    )
    op.create_index(
        "pending_approvals_one_open",
        "pending_approvals",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("models_tried", ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("est_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('reply', 'confirmation', 'handoff', 'degraded', 'error')",
            name="agent_runs_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("pending_approvals")
    op.drop_table("messages")
    op.drop_table("conversations")
