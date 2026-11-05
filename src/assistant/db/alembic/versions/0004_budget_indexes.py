"""Indexes for the budgets: the last day's runs, the service's and a conversation's.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("agent_runs_started_at", "agent_runs", ["started_at"])
    op.create_index(
        "agent_runs_conversation_started_at", "agent_runs", ["conversation_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("agent_runs_conversation_started_at", table_name="agent_runs")
    op.drop_index("agent_runs_started_at", table_name="agent_runs")
