"""correlation_id on decisions + trades (decision-trace lineage)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.add_column("trades", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.create_index("ix_decisions_correlation_id", "decisions", ["correlation_id"])
    op.create_index("ix_trades_correlation_id", "trades", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_correlation_id", table_name="trades")
    op.drop_index("ix_decisions_correlation_id", table_name="decisions")
    op.drop_column("trades", "correlation_id")
    op.drop_column("decisions", "correlation_id")
