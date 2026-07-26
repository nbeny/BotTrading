"""events_market hypertable

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events_market",
        # `time` is in the primary key because TimescaleDB refuses any unique
        # index that omits the partitioning column -- verified against the
        # production database. (time, event_id) is equally effective for Kafka
        # idempotence: a redelivered message carries an identical serialized
        # event, so both columns match.
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    # No index on (time, event_id): PRIMARY KEY (time, event_id) is already
    # backed by a btree on exactly those columns, and a btree scans backwards,
    # so it serves ORDER BY time DESC, event_id DESC. A duplicate would cost
    # write throughput per chunk on the busiest table for no read benefit.
    op.create_index("ix_events_market_symbol", "events_market", ["symbol", "time"])
    op.create_index(
        "ix_events_market_correlation",
        "events_market",
        ["correlation_id"],
        postgresql_where=sa.text("correlation_id IS NOT NULL"),
    )
    op.execute(
        "SELECT create_hypertable('events_market', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    # 7 days: price/volume/dex are the high-volume tier and are only ever read
    # back over a short window.
    op.execute(
        "SELECT add_retention_policy('events_market', INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    # `if_exists`, not `if_not_exists`: the latter belongs to
    # add_retention_policy, and the wrong keyword raises before the DROP TABLE.
    op.execute("SELECT remove_retention_policy('events_market', if_exists => TRUE)")
    op.drop_table("events_market")
