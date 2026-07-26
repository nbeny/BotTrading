"""decision_journal hypertable

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_journal",
        # `time` is in the primary key because TimescaleDB rejects
        # create_hypertable when the partitioning column is absent from it.
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("signal_event_id", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        # état décisionnel
        sa.Column("factors", postgresql.JSONB, server_default="{}"),
        sa.Column("features", postgresql.JSONB, server_default="{}"),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("factors_present", sa.SmallInteger, nullable=False),
        sa.Column("escalated", sa.Boolean, server_default=sa.false(), nullable=False),
        # contexte d'entrée
        sa.Column("entry_price", sa.Numeric(38, 12)),
        sa.Column("stop_loss", sa.Numeric(38, 12)),
        sa.Column("take_profit", sa.Numeric(38, 12)),
        sa.Column("risk_reward_ratio", sa.Float),
        sa.Column("volatility_1h", sa.Float),
        sa.Column("volatility_24h", sa.Float),
        sa.Column("dominant_factor", sa.String(16)),
        sa.Column("dominant_factor_share", sa.Float),
        sa.Column("market_cap_rank", sa.Integer),
        # verdict analyste
        sa.Column(
            "sonnet_called", sa.Boolean, server_default=sa.false(), nullable=False
        ),
        sa.Column("sonnet_validated", sa.Boolean),
        sa.Column("sonnet_score", sa.Integer),
        sa.Column("sonnet_confidence", sa.Float),
        sa.Column("sonnet_direction", sa.String(8)),
        sa.Column("skip_reason", sa.String(64)),
        # moitié B — alimentée par le chantier de déduplication
        sa.Column("cooldown_verdict", sa.Boolean),
        sa.Column("dedup_verdict", sa.Boolean),
        sa.Column("dedup_trigger", sa.String(8)),
        sa.Column("drift_momentum", sa.Float),
        sa.Column("drift_volume", sa.Float),
        sa.Column("drift_sentiment", sa.Float),
        sa.Column("drift_liquidity", sa.Float),
        sa.Column("sign_flip_chg", sa.Boolean),
        sa.Column("sign_flip_sentiment", sa.Boolean),
        sa.Column("score_anchor", sa.Integer),
        sa.Column("factors_present_anchor", sa.SmallInteger),
        sa.Column("seconds_since_anchor", sa.Integer),
        sa.Column("regime", sa.String(16)),
        sa.Column("regime_anchor", sa.String(16)),
        sa.Column("dedup_version", sa.String(32)),
        sa.Column("dedup_quantile", sa.Float),
        sa.Column("dedup_deltas", postgresql.JSONB, server_default="{}"),
        # aval
        sa.Column("decision_event_id", sa.String(64)),
        sa.Column("risk_event_id", sa.String(64)),
        sa.Column("risk_verdict", sa.String(16)),
        sa.Column("risk_reason", sa.Text),
        sa.Column("execution_event_id", sa.String(64)),
        sa.Column("execution_kind", sa.String(16)),
        sa.Column("fill_price", sa.Numeric(38, 12)),
        sa.Column("realized_pnl", sa.Numeric(38, 12)),
    )
    op.create_index("ix_journal_symbol_time", "decision_journal", ["symbol", "time"])
    op.create_index("ix_journal_correlation", "decision_journal", ["correlation_id"])
    # Partial index: the completion path looks up only rows that carry a
    # decision, which is a small minority of the table.
    op.create_index(
        "ix_journal_decision_event",
        "decision_journal",
        ["decision_event_id"],
        postgresql_where=sa.text("decision_event_id IS NOT NULL"),
    )
    op.create_index(
        "ix_journal_risk_event",
        "decision_journal",
        ["risk_event_id"],
        postgresql_where=sa.text("risk_event_id IS NOT NULL"),
    )
    op.execute(
        "SELECT create_hypertable('decision_journal', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('decision_journal', INTERVAL '180 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    # remove_retention_policy's guard argument is `if_exists`, not
    # `if_not_exists` (that spelling belongs to add_retention_policy) —
    # passing the wrong name aborts the downgrade with an unknown-parameter
    # error before the DROP TABLE ever runs.
    op.execute("SELECT remove_retention_policy('decision_journal', if_exists => TRUE)")
    op.drop_table("decision_journal")
