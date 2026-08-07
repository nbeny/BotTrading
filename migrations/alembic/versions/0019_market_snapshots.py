"""derivatives/fundamentals/developer snapshot hypertables

Revision ID: 0019
Revises: 0018

Numbered 0019, not the 0011 the plan named: the branch this migration was
authored on forked from 0010, but master had already grown 0011..0018 by the
time this landed (github_activity, market_data_foundation, etc.). Reusing
0011 would give alembic two revisions with the same id and two heads --
`upgrade head` refuses to migrate in that state, in production, same trap
0018's docstring records.
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `time` is in the primary key because TimescaleDB refuses any unique
    # index that omits the partitioning column -- same trap as events_market
    # (0010). (time, symbol) is the natural key here: one row per republication.
    op.create_table(
        "derivatives_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("venue", sa.String(16), nullable=False, server_default="binance"),
        sa.Column("funding_rate_8h", sa.Float),
        sa.Column("funding_annualized_pct", sa.Float),
        sa.Column("open_interest_usd", sa.Numeric(38, 2)),
        sa.Column("open_interest_change_pct_24h", sa.Float),
        sa.Column("long_short_account_ratio", sa.Float),
    )
    op.create_index(
        "ix_derivatives_snapshots_symbol",
        "derivatives_snapshots",
        ["symbol", "time"],
    )
    op.execute(
        "SELECT create_hypertable('derivatives_snapshots', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('derivatives_snapshots', INTERVAL '90 days', "
        "if_not_exists => TRUE)"
    )

    op.create_table(
        "fundamentals_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("coin_id", sa.String(64), nullable=False),
        sa.Column("tvl_usd", sa.Numeric(38, 2)),
        sa.Column("tvl_change_pct_7d", sa.Float),
        sa.Column("fees_24h_usd", sa.Numeric(38, 2)),
        sa.Column("fees_change_pct_7d", sa.Float),
        sa.Column("next_unlock_at", sa.DateTime(timezone=True)),
        sa.Column("next_unlock_pct_supply", sa.Float),
        sa.Column(
            "has_unlock_schedule", sa.Boolean, nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_fundamentals_snapshots_symbol",
        "fundamentals_snapshots",
        ["symbol", "time"],
    )
    op.execute(
        "SELECT create_hypertable('fundamentals_snapshots', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('fundamentals_snapshots', INTERVAL '90 days', "
        "if_not_exists => TRUE)"
    )

    op.create_table(
        "developer_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("coin_id", sa.String(64), nullable=False),
        sa.Column("repo_count", sa.Integer, nullable=False),
        sa.Column("commit_ratio_4w", sa.Float),
        sa.Column("pr_ratio_4w", sa.Float),
        sa.Column("days_since_push", sa.Integer),
        sa.Column("star_growth_pct_7d", sa.Float),
        sa.Column(
            "all_repos_archived", sa.Boolean, nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_developer_snapshots_symbol",
        "developer_snapshots",
        ["symbol", "time"],
    )
    op.execute(
        "SELECT create_hypertable('developer_snapshots', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('developer_snapshots', INTERVAL '90 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    # `if_exists`, not `if_not_exists`: the latter belongs to
    # add_retention_policy, and the wrong keyword raises before the DROP TABLE.
    op.execute(
        "SELECT remove_retention_policy('developer_snapshots', if_exists => TRUE)"
    )
    op.drop_table("developer_snapshots")

    op.execute(
        "SELECT remove_retention_policy('fundamentals_snapshots', if_exists => TRUE)"
    )
    op.drop_table("fundamentals_snapshots")

    op.execute(
        "SELECT remove_retention_policy('derivatives_snapshots', if_exists => TRUE)"
    )
    op.drop_table("derivatives_snapshots")
