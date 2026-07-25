"""service_health (latest health probe per service)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_health",
        sa.Column("service", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), server_default="healthy"),
        sa.Column("healthy", sa.Boolean, server_default=sa.true()),
        sa.Column("latency_ms", sa.Float, server_default="0"),
        sa.Column("detail", postgresql.JSONB, server_default="{}"),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("service_health")
