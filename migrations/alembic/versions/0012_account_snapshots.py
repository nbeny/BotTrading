"""account_snapshots

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-26

Deliberately **not** a hypertable: one row per venue per minute is 1440 rows a
day for one venue. Time partitioning would buy nothing at that volume and would
complicate the only query there is — the latest state for a venue.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # UNIQUE, not just indexed: it is what ON CONFLICT DO NOTHING infers on,
        # and Kafka is at-least-once — a redelivered message carries an
        # identical event and must not become a second row.
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("equity_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("cash_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("balances", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Serves the only query there is: the latest snapshot for a venue.
    # DESC is cosmetic here — a btree scans backwards, so a plain
    # (venue, fetched_at) would answer ORDER BY fetched_at DESC within a venue
    # at the same cost. It is kept because it states the access pattern; the
    # rendered DDL was checked to be `(venue, fetched_at DESC)`.
    op.create_index(
        "ix_account_snapshots_venue_time",
        "account_snapshots",
        ["venue", sa.text("fetched_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_account_snapshots_venue_time", table_name="account_snapshots")
    op.drop_table("account_snapshots")
