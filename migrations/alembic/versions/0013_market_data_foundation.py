"""candles / market_depth / venue_pairs

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candles",
        # `time` leads the primary key: TimescaleDB refuses any unique index
        # that omits the partitioning column (same constraint as events_market).
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("interval", sa.String(8), primary_key=True),
        sa.Column("open", sa.Numeric(38, 12), nullable=False),
        sa.Column("high", sa.Numeric(38, 12), nullable=False),
        sa.Column("low", sa.Numeric(38, 12), nullable=False),
        sa.Column("close", sa.Numeric(38, 12), nullable=False),
        sa.Column("vwap", sa.Numeric(38, 12)),
        sa.Column("volume", sa.Numeric(38, 12), server_default="0"),
        sa.Column("trades", sa.Integer, server_default="0"),
        sa.Column("source", sa.String(32), server_default="kraken"),
    )
    # Serves "last N candles of symbol X at interval Y", the reader's only shape.
    op.create_index(
        "ix_candles_symbol_interval", "candles", ["symbol", "interval", "time"]
    )
    op.execute(
        "SELECT create_hypertable('candles', 'time', "
        "chunk_time_interval => INTERVAL '7 days', migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('candles', INTERVAL '90 days', "
        "if_not_exists => TRUE)"
    )

    op.create_table(
        "market_depth",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("mid_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("spread_pct", sa.Float, nullable=False),
        sa.Column("bid_depth_usd", sa.Numeric(38, 2), nullable=False),
        sa.Column("ask_depth_usd", sa.Numeric(38, 2), nullable=False),
        sa.Column("source", sa.String(32), server_default="kraken"),
    )
    op.create_index("ix_market_depth_symbol", "market_depth", ["symbol", "time"])
    op.execute(
        "SELECT create_hypertable('market_depth', 'time', "
        "chunk_time_interval => INTERVAL '1 day', migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('market_depth', INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    )

    # Reference data, not a time series: a plain table, no hypertable.
    op.create_table(
        "venue_pairs",
        sa.Column("venue", sa.String(32), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("pair", sa.String(64), nullable=False),
        sa.Column("ordermin", sa.Numeric(38, 12)),
        sa.Column("tradable", sa.Boolean, server_default=sa.true()),
        sa.Column("ambiguous", sa.Boolean, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("venue_pairs")
    # Retention policies must go before the table; remove_retention_policy takes
    # `if_exists`, and the wrong keyword raises before the DROP TABLE runs.
    op.execute("SELECT remove_retention_policy('market_depth', if_exists => TRUE)")
    op.drop_index("ix_market_depth_symbol", table_name="market_depth")
    op.drop_table("market_depth")
    op.execute("SELECT remove_retention_policy('candles', if_exists => TRUE)")
    op.drop_index("ix_candles_symbol_interval", table_name="candles")
    op.drop_table("candles")
