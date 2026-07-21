"""trade execution columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("kraken_order_id", sa.String(64), nullable=True))
    op.add_column("trades", sa.Column("fill_price", sa.Float, nullable=True))
    op.add_column("trades", sa.Column("pnl", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "pnl")
    op.drop_column("trades", "fill_price")
    op.drop_column("trades", "kraken_order_id")
