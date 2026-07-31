"""indexes for the Command Center per-stage volume queries

/systems/overview is polled every 8s and now counts rows over a 1h/24h/7d
window on four tables. Without these, the 7d window sequentially scans
raw_content on every cache miss.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_raw_content_fetched_at", "raw_content", ["fetched_at"])
    op.create_index("ix_raw_content_scored_at", "raw_content", ["scored_at"])
    op.create_index("ix_decisions_created_at", "decisions", ["created_at"])
    op.create_index("ix_trades_created_at", "trades", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_trades_created_at", table_name="trades")
    op.drop_index("ix_decisions_created_at", table_name="decisions")
    op.drop_index("ix_raw_content_scored_at", table_name="raw_content")
    op.drop_index("ix_raw_content_fetched_at", table_name="raw_content")
