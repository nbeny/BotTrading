"""content_sentiment_agg → additive hourly buckets + raw_content author indexes

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No readers of the old table and its data is recomputable → drop/recreate.
    op.drop_table("content_sentiment_agg")
    op.create_table(
        "content_sentiment_agg",
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
    # Support the read-time distinct-author query (symbols @> '["BTC"]').
    op.create_index(
        "ix_raw_content_symbols_gin", "raw_content", ["symbols"],
        postgresql_using="gin",
    )
    op.create_index("ix_raw_content_published_at", "raw_content", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_raw_content_published_at", table_name="raw_content")
    op.drop_index("ix_raw_content_symbols_gin", table_name="raw_content")
    op.drop_table("content_sentiment_agg")
    op.create_table(
        "content_sentiment_agg",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("window_size", sa.Integer, primary_key=True),
        sa.Column("mentions", sa.Integer, server_default="0"),
        sa.Column("unique_authors", sa.Integer, server_default="0"),
        sa.Column("engagement_sum", sa.Float, server_default="0"),
        sa.Column("avg_sentiment", sa.Float, server_default="0"),
        sa.Column("weighted_sentiment", sa.Float, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
