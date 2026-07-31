"""drop the writer-less `news` and `sentiments` tables

Both were declared, imported and read, but no code ever wrote either. Their only
effect was to make /market/news and every token's sentiment_score look like real,
empty data — a shape-conformant lie the contract test could not catch.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("news")
    op.drop_table("sentiments")


def downgrade() -> None:
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
        "news",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("article_id", sa.String(128), unique=True, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("published_at", sa.BigInteger, nullable=False),
        sa.Column("symbols", postgresql.JSONB, server_default="[]"),
        sa.Column("provider_sentiment", sa.Float),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
