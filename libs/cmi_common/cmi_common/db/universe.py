"""Which symbols the platform considers live, and which of them are majors.

Both are derived from data on every call, never hard-coded. Shared rather than
per-collector: collector-kraken and collector-binance-futures both need the
majors set, and two copies of the definition would drift the moment one of them
was tuned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ContentSentimentAgg, Price

#: Knee of the observed mention distribution (measured 2026-07-29: 11 symbols
#: clear 10 over 7 days, the next tier sits in single digits).
DEFAULT_MIN_MENTIONS = 10


def majors(
    symbols: set[str],
    mentions: dict[str, int],
    *,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> set[str]:
    """Majors have enough sentiment coverage to fuse on, and consequently are
    the only symbols worth spending per-symbol API budget on — which is why a
    second collector needs this same set.
    """
    return {s for s in symbols if mentions.get(s, 0) >= min_mentions}


async def priced_symbols(session: AsyncSession, *, hours: int = 24) -> set[str]:
    since = datetime.now(tz=UTC) - timedelta(hours=hours)
    stmt = select(Price.symbol).where(Price.time >= since).distinct()
    return set((await session.execute(stmt)).scalars().all())


async def mention_counts(session: AsyncSession, *, days: int = 7) -> dict[str, int]:
    """Mentions per symbol over the window, both kinds summed."""
    since = datetime.now(tz=UTC) - timedelta(days=days)
    stmt = (
        select(ContentSentimentAgg.symbol, func.sum(ContentSentimentAgg.mentions))
        .where(ContentSentimentAgg.bucket_start >= since)
        .group_by(ContentSentimentAgg.symbol)
    )
    return {sym: int(total or 0) for sym, total in (await session.execute(stmt)).all()}
