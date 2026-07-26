"""signal diagnostics columns + pipeline_rejections

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only, and every added column carries a server_default: `signals`
    # is a populated hypertable in production, so a NOT NULL column without a
    # default would abort the deploy. The defaults also keep rows written by an
    # older api-gateway valid during the rolling window.
    op.add_column(
        "signals",
        sa.Column("ambiguous", sa.Boolean, server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "signals",
        sa.Column(
            "block_reason", sa.String(32), server_default="unknown", nullable=False
        ),
    )
    op.add_column(
        "signals",
        sa.Column("factors_present", sa.Integer, server_default="0", nullable=False),
    )
    op.create_index("ix_signals_block_reason", "signals", ["block_reason"])

    # `time` is part of the primary key because TimescaleDB requires the
    # partitioning column to be present in it before create_hypertable succeeds.
    # timezone=True matches every other hypertable partition column in this repo
    # (prices/sentiments/signals in 0001), so funnel queries can compare the two
    # tables' timestamps without an implicit session-timezone cast.
    op.create_table(
        "pipeline_rejections",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_pipeline_rejections_stage_time", "pipeline_rejections", ["stage", "time"]
    )
    op.execute(
        "SELECT create_hypertable('pipeline_rejections', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("pipeline_rejections")
    op.drop_index("ix_signals_block_reason", table_name="signals")
    op.drop_column("signals", "factors_present")
    op.drop_column("signals", "block_reason")
    op.drop_column("signals", "ambiguous")
