"""initial schema + timescale hypertables

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("coin_id", sa.String(128), unique=True),
        sa.Column("name", sa.String(128)),
        sa.Column("chain", sa.String(64)),
        sa.Column("address", sa.String(128), index=True),
        sa.Column("is_blacklisted", sa.Boolean, server_default=sa.false()),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "chain", name="symbol_chain"),
    )

    op.create_table(
        "prices",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("price_usd", sa.Numeric(38, 12), nullable=False),
        sa.Column("market_cap_usd", sa.Numeric(38, 2)),
        sa.Column("volume_24h_usd", sa.Numeric(38, 2)),
        sa.Column("price_change_pct_24h", sa.Float),
        sa.Column("source", sa.String(32), nullable=False),
    )
    op.create_index("ix_prices_symbol_time", "prices", ["symbol", "time"])

    op.create_table(
        "news",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("article_id", sa.String(128), unique=True, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("published_at", sa.BigInteger, nullable=False),
        sa.Column("symbols", postgresql.JSONB, server_default="[]"),
        sa.Column("provider_sentiment", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "sentiments",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("input_kind", sa.String(16), primary_key=True),
        sa.Column("sentiment_score", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
    )

    op.create_table(
        "signals",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("opportunity_score", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("escalated", sa.Boolean, server_default=sa.false()),
        sa.Column("payload", postgresql.JSONB, server_default="{}"),
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.String(64), unique=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("opportunity_score", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("ai_validated", sa.Boolean, server_default=sa.false()),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.String(64), unique=True, nullable=False),
        sa.Column("decision_id", sa.Integer, sa.ForeignKey("decisions.id")),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("stop_loss", sa.Float, nullable=False),
        sa.Column("take_profit", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("position_size_pct", sa.Float, server_default="0"),
        sa.Column("risk_reward_ratio", sa.Float, server_default="0"),
        sa.Column("status", sa.String(16), server_default="approved"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Convert time-series tables into Timescale hypertables.
    for table, col in (("prices", "time"), ("sentiments", "time"), ("signals", "time")):
        op.execute(
            f"SELECT create_hypertable('{table}', '{col}', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )


def downgrade() -> None:
    for table in ("trades", "decisions", "signals", "sentiments", "news", "prices", "tokens"):
        op.drop_table(table)
