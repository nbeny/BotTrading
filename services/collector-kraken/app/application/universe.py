"""Which symbols we sweep, and which of them are majors.

Both are derived from data on every cycle, never hard-coded: a token that starts
being talked about joins the majors set on its own, and one that goes quiet
leaves it. The pure selection functions are unit-tested; the two SQL helpers
supply their inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db.models import ContentSentimentAgg, Price, Token

from ..domain.pairs import VenuePairSpec

#: Knee of the observed mention distribution (measured 2026-07-29: 11 symbols
#: clear 10 over 7 days, the next tier sits in single digits). See the spec.
DEFAULT_MIN_MENTIONS = 10


def intersect(
    specs: list[VenuePairSpec], priced_symbols: set[str]
) -> list[VenuePairSpec]:
    """Kraken-tradable pairs we also have prices for."""
    return [s for s in specs if s.symbol in priced_symbols]


def split_regimes(
    specs: list[VenuePairSpec],
    *,
    mentions: dict[str, int],
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> tuple[list[VenuePairSpec], list[VenuePairSpec]]:
    """(majors, alts) — majors are sentiment-covered enough to fuse on."""
    majors = [s for s in specs if mentions.get(s.symbol, 0) >= min_mentions]
    major_symbols = {s.symbol for s in majors}
    return majors, [s for s in specs if s.symbol not in major_symbols]


def ambiguous_symbols(rows: list[tuple[str, str, int | None]]) -> set[str]:
    """Tickers claimed by more than one coin, from (symbol, coin_id, rank) rows.

    Recorded rather than silently resolved: `venue_pairs.ambiguous` leaves a
    trace an operator can query, instead of a mapping that looks certain.
    """
    seen: dict[str, set[str]] = {}
    for symbol, coin_id, _rank in rows:
        seen.setdefault(symbol, set()).add(coin_id)
    return {symbol for symbol, coins in seen.items() if len(coins) > 1}


def untradable(specs: list[VenuePairSpec], priced: set[str]) -> set[str]:
    """Symbols we price but Kraken does not list — the "cannot trade this" set."""
    return priced - {s.symbol for s in specs}


async def priced_symbols(session: AsyncSession, *, hours: int = 24) -> set[str]:
    since = datetime.now(tz=UTC) - timedelta(hours=hours)
    stmt = select(Price.symbol).where(Price.time >= since).distinct()
    return {row for row in (await session.execute(stmt)).scalars().all()}


async def mention_counts(session: AsyncSession, *, days: int = 7) -> dict[str, int]:
    """Mentions per symbol over the window, both kinds summed."""
    since = datetime.now(tz=UTC) - timedelta(days=days)
    stmt = (
        select(ContentSentimentAgg.symbol, func.sum(ContentSentimentAgg.mentions))
        .where(ContentSentimentAgg.bucket_start >= since)
        .group_by(ContentSentimentAgg.symbol)
    )
    return {sym: int(total or 0) for sym, total in (await session.execute(stmt)).all()}


async def token_symbol_ranks(
    session: AsyncSession,
) -> list[tuple[str, str, int | None]]:
    """(symbol, coin_id, market_cap_rank) for every known token."""
    stmt = select(Token.symbol, Token.coin_id, Token.metadata_)
    return [
        (symbol, coin_id or "", (meta or {}).get("market_cap_rank"))
        for symbol, coin_id, meta in (await session.execute(stmt)).all()
    ]
