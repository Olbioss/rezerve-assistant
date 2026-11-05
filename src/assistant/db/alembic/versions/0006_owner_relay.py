"""The owner relay: who owns each business's handoffs, and which owner
message speaks for which conversation.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "business_config",
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("owner_telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("slug"),
    )
    op.create_table(
        "owner_relays",
        sa.Column("owner_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_chat_id", "telegram_message_id"),
    )


def downgrade() -> None:
    op.drop_table("owner_relays")
    op.drop_table("business_config")
    op.drop_column("conversations", "handed_off_at")
