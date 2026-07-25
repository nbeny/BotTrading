"""content_sentiment_agg_daily — daily rollup tier for aged-out hourly buckets

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_sentiment_agg_daily",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("mentions", sa.Integer, server_default="0"),
        sa.Column("score_sum", sa.Float, server_default="0"),
        sa.Column("confidence_sum", sa.Float, server_default="0"),
        sa.Column("weighted_score_sum", sa.Float, server_default="0"),
        sa.Column("engagement_sum", sa.Float, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("content_sentiment_agg_daily")
