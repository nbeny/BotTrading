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
    # 0006 already created this index name, with the default jsonb_ops. Changing
    # the opclass means *replacing* the index, not adding one -- without this
    # drop the migration raises DuplicateTable on every database that followed
    # the chain, which is every deployed one. IF EXISTS rather than op.drop_index
    # so a database that somehow lacks it still upgrades.
    op.execute("DROP INDEX IF EXISTS ix_raw_content_symbols_gin")
    op.create_index(
        "ix_raw_content_symbols_gin",
        "raw_content",
        ["symbols"],
        postgresql_using="gin",
        postgresql_ops={"symbols": "jsonb_path_ops"},
    )


def downgrade() -> None:
    # Restore 0006's index rather than leaving none: revision 0015 expects the
    # name to exist, and dropping it outright would make the downgrade land on a
    # schema no upgrade path ever produces.
    op.execute("DROP INDEX IF EXISTS ix_raw_content_symbols_gin")
    op.create_index(
        "ix_raw_content_symbols_gin",
        "raw_content",
        ["symbols"],
        postgresql_using="gin",
    )
