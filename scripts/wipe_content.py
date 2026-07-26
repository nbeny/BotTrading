#!/usr/bin/env python3
"""Phase 2: drop all collected content so it can be re-gathered under the gate.

Every symbol attribution stored before the normalization layer shipped was
produced by trusting upstream coin tags verbatim. Measured on production, 62% of
those attributions named a coin that appears nowhere in the article's own text
(`ONE` 24, `NEAR` 17, `JST` 9, `KEEP` 8 in a 161-row sample). The read windows
reach five years, so that noise does not age out on its own -- it has to go.

Truncates `raw_content` and both sentiment rollups. The collectors repopulate
from scratch within minutes; the rollups rebuild as the sentiment worker scores
the new rows.

Run ONLY after the gate is confirmed working in production. Wiping first would
just re-pollute the table.

Usage:
    python scripts/wipe_content.py              # dry run, prints what it would do
    python scripts/wipe_content.py --confirm    # actually destroys
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TABLES = ("raw_content", "content_sentiment_agg", "content_sentiment_agg_daily")


async def _counts(engine) -> dict[str, int]:  # type: ignore[no-untyped-def]
    out: dict[str, int] = {}
    async with engine.connect() as conn:
        for table in TABLES:
            result = await conn.execute(text(f"SELECT count(*) FROM {table}"))
            out[table] = int(result.scalar_one())
    return out


async def _truncate(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        # One statement so the three tables are emptied atomically: a partial
        # wipe would leave rollups referencing rows that no longer exist.
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)}"))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually truncate; without it the script only reports",
    )
    args = parser.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_async_engine(dsn)
    try:
        before = await _counts(engine)
        total = sum(before.values())
        print("About to destroy:")
        for table, rows in before.items():
            print(f"  {table:32} {rows:>8} rows")
        print(f"  {'TOTAL':32} {total:>8} rows")

        if not args.confirm:
            print("\nDry run. Re-run with --confirm to destroy these rows.")
            return 0

        await _truncate(engine)
        after = await _counts(engine)
        print("\nAfter:")
        for table, rows in after.items():
            print(f"  {table:32} {rows:>8} rows")
        if any(after.values()):
            print("\nWARNING: tables are not empty", file=sys.stderr)
            return 1
        print("\nDone. Collectors will repopulate under the relevance gate.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
