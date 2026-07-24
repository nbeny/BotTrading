"""raw_content + content_sentiment_agg

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_content",
        sa.Column("id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("author", sa.String(256)),
        sa.Column("title", sa.Text),
        sa.Column("text", sa.Text, server_default=""),
        sa.Column("symbols", postgresql.JSONB, server_default="[]"),
        sa.Column("engagement", sa.Float),
        sa.Column("lang", sa.String(16)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("sentiment_score", sa.Float),
        sa.Column("sentiment_confidence", sa.Float),
        sa.Column("sentiment_model", sa.String(128)),
        sa.Column("scored_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", "fetched_at"),
        sa.UniqueConstraint("source", "external_id",
                            name="uq_raw_content_source_external"),
    )
    op.create_index(
        "ix_raw_content_unscored", "raw_content", ["fetched_at"],
        postgresql_where=sa.text("scored_at IS NULL"),
    )
    op.execute(
        "SELECT create_hypertable('raw_content', 'fetched_at', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )

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


def downgrade() -> None:
    op.drop_table("content_sentiment_agg")
    op.drop_table("raw_content")
