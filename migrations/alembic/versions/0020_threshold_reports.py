"""threshold_reports

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Table simple, pas d'hypertable : quelques lignes par jour, lues par
    # `ORDER BY time DESC LIMIT 1`. Un chunking Timescale ici n'apporterait rien
    # et compliquerait le downgrade.
    op.create_table(
        "threshold_reports",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("window_days", sa.Integer, nullable=False),
        sa.Column("target_per_day", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text),
        sa.Column("duration_s", sa.Float),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("threshold_reports")
