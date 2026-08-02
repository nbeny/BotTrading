"""Index the correlation lookup that /trace/{cid} runs on `signals`

`signals` is the one table in the trace path with no index on the column the
lookup filters by: `decisions` and `trades` both carry an indexed
`correlation_id` column, while the analysis stage stores it inside `payload`
and was matched with `payload->>'correlation_id' = :cid`.

Measured in production on 2026-08-02, 1_485_471 rows across two chunks: a
*miss* costs a full scan of the hypertable -- 127.5 s, 1.48 M buffers. The
axios client gives the request 15 s, so every trace of an event that never
reached an analysis (95% of the feed: raw price/volume/dex and sentiment
events each carry their own correlation id) timed out and rendered an empty
drawer, after burning two minutes of database CPU per click.

A functional B-tree on the extracted text is exact for `=` and needs no schema
change or backfill. It is not created CONCURRENTLY: Alembic runs migrations in
a transaction, which forbids it. The build takes a ShareLock on each chunk, so
persister inserts into the live chunk block for its duration (tens of seconds
at this size) and then catch up from Kafka.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw SQL rather than op.create_index: the target is an expression, not a
    # column, which the Alembic helper cannot express.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_signals_correlation "
        "ON signals ((payload ->> 'correlation_id'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_signals_correlation")
