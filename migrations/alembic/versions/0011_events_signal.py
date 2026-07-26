"""events_signal hypertable

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-26

Same shape as ``events_market``; the two tables are separated **only** by
retention, because ``add_retention_policy`` drops whole chunks by time and
cannot filter by event type. A single table with differentiated retention would
need a scheduled ``DELETE ... WHERE event_type IN (...)``, a costly scan on a
hot table where a chunk drop is nearly free.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events_signal",
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
    op.create_index(
        "ix_events_signal_page",
        "events_signal",
        ["time", "event_id"],
        postgresql_using="btree",
    )
    op.create_index("ix_events_signal_symbol", "events_signal", ["symbol", "time"])
    op.create_index(
        "ix_events_signal_correlation",
        "events_signal",
        ["correlation_id"],
        postgresql_where=sa.text("correlation_id IS NOT NULL"),
    )
    op.execute(
        "SELECT create_hypertable('events_signal', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    # 90 days: sentiment/analysis/decision/risk/execution are low volume and are
    # the ones worth looking back at.
    op.execute(
        "SELECT add_retention_policy('events_signal', INTERVAL '90 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    # `if_exists`, not `if_not_exists`: the latter belongs to
    # add_retention_policy, and the wrong keyword raises before the DROP TABLE.
    op.execute("SELECT remove_retention_policy('events_signal', if_exists => TRUE)")
    op.drop_table("events_signal")
