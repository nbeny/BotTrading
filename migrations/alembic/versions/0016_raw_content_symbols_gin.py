"""GIN index for raw_content.symbols JSONB containment lookups

Two hot paths filter raw_content by `symbols @> '[...]'`: the data explorer
(`data_content`) and the market token dossier (`market_token_dossier`), which
fires on every drawer open -- i.e. every row click on /market. raw_content is
the highest-volume table in the schema, so each of those containment checks
was a sequential scan. Uses jsonb_path_ops rather than the default jsonb_ops:
both call sites only ever use `@>`, and jsonb_path_ops is smaller and faster
for that operator specifically (at the cost of not supporting `?`/`?|`/`?&`,
which nothing here needs).

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_raw_content_symbols_gin",
        "raw_content",
        ["symbols"],
        postgresql_using="gin",
        postgresql_ops={"symbols": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_raw_content_symbols_gin", table_name="raw_content")
