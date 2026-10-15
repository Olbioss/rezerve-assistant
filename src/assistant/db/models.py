"""The agent's own tables (spec §7.8). Phase 2 needs these four; the owner
relay, reminders and the daily usage counter arrive with their phases.

`sa.text` is spelled out because `messages.text` is a column: inside a
class body a bare `text(...)` would call the column, not SQLAlchemy.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: sa.DateTime(timezone=True)}


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    business_slug: Mapped[str] = mapped_column(sa.Text)
    channel: Mapped[str] = mapped_column(sa.Text)
    channel_user_id: Mapped[str] = mapped_column(sa.Text)
    customer_ref: Mapped[str] = mapped_column(sa.Text)
    state: Mapped[str] = mapped_column(sa.Text, server_default="active")
    # Pydantic AI messages (ModelMessagesTypeAdapter), trimmed.
    history: Mapped[list[Any]] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    # The lease: who is working on this conversation, and until when.
    locked_until: Mapped[datetime | None]
    lease_owner: Mapped[str | None] = mapped_column(sa.Text)
    last_message_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())

    __table_args__ = (
        sa.CheckConstraint(
            "channel IN ('web', 'telegram', 'whatsapp')", name="conversations_channel"
        ),
        sa.CheckConstraint("state IN ('active', 'human', 'closed')", name="conversations_state"),
        # One open conversation per customer and business; closed ones are history.
        sa.Index(
            "conversations_open_per_user",
            "business_slug",
            "channel",
            "channel_user_id",
            unique=True,
            postgresql_where=sa.text("state <> 'closed'"),
        ),
        sa.Index("conversations_last_message_at", "last_message_at"),
    )


class Message(Base):
    __tablename__ = "messages"

    seq: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(sa.Text)
    direction: Mapped[str] = mapped_column(sa.Text)
    # Inbound: an id unique across the whole channel (Telegram's update_id,
    # WhatsApp's wamid, the widget's client_message_id).
    channel_message_id: Mapped[str | None] = mapped_column(sa.Text)
    # Outbound: the id the channel gave the sent message.
    sent_message_id: Mapped[str | None] = mapped_column(sa.Text)
    text: Mapped[str] = mapped_column(sa.Text)
    actions: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    processed_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]

    __table_args__ = (
        sa.CheckConstraint("direction IN ('in', 'out', 'owner')", name="messages_direction"),
        # A redelivered webhook is stored once.
        sa.Index(
            "messages_inbound_once",
            "channel",
            "channel_message_id",
            unique=True,
            postgresql_where=sa.text("direction = 'in'"),
        ),
        sa.Index("messages_conversation_seq", "conversation_id", "seq"),
    )


class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="CASCADE")
    )
    tool_call_id: Mapped[str] = mapped_column(sa.Text)
    tool_name: Mapped[str] = mapped_column(sa.Text)
    # The arguments that will run if approved, the shown terms pinned in.
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Other writes the model proposed in the same breath; denied on resume.
    other_call_ids: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), server_default=sa.text("'{}'")
    )
    rendered_text: Mapped[str] = mapped_column(sa.Text)
    history: Mapped[list[Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    expires_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    decision: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.CheckConstraint(
            "decision IN ('approved', 'denied', 'expired')", name="pending_approvals_decision"
        ),
        sa.Index(
            "pending_approvals_one_open",
            "conversation_id",
            unique=True,
            postgresql_where=sa.text("resolved_at IS NULL"),
        ),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    # Metrics outlive the conversation: pruning a transcript keeps its numbers.
    conversation_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime]
    duration_ms: Mapped[int]
    model_used: Mapped[str | None] = mapped_column(sa.Text)
    models_tried: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), server_default=sa.text("'{}'"))
    requests: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    tool_calls: Mapped[int]
    est_cost_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 6))
    outcome: Mapped[str] = mapped_column(sa.Text)
    trace_id: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.CheckConstraint(
            "outcome IN ('reply', 'confirmation', 'handoff', 'degraded', 'error')",
            name="agent_runs_outcome",
        ),
    )
