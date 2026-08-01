"""Which symbols we sweep, and which of them are majors.

Both are derived from data on every cycle, never hard-coded: a token that starts
being talked about joins the majors set on its own, and one that goes quiet
leaves it. The SQL helpers and the mention floor now live in
`cmi_common.db.universe` and are re-exported here so this module stays the
single import site for Kraken's sweep.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db.models import Token
from cmi_common.db.universe import (
    DEFAULT_MIN_MENTIONS,
    majors,
    mention_counts,
    priced_symbols,
)

from ..domain.pairs import VenuePairSpec

__all__ = [
    "DEFAULT_MIN_MENTIONS",
    "ambiguous_symbols",
    "intersect",
    "majors",
    "mention_counts",
    "priced_symbols",
    "split_regimes",
    "token_symbol_ranks",
    "untradable",
]


def intersect(specs: list[VenuePairSpec], priced: set[str]) -> list[VenuePairSpec]:
    """Kraken-tradable pairs we also have prices for."""
    return [s for s in specs if s.symbol in priced]


def split_regimes(
    specs: list[VenuePairSpec],
    *,
    mentions: dict[str, int],
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> tuple[list[VenuePairSpec], list[VenuePairSpec]]:
    """(majors, alts) — majors are sentiment-covered enough to fuse on."""
    major_symbols = majors(
        {s.symbol for s in specs}, mentions, min_mentions=min_mentions
    )
    return (
        [s for s in specs if s.symbol in major_symbols],
        [s for s in specs if s.symbol not in major_symbols],
    )


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


async def token_symbol_ranks(
    session: AsyncSession,
) -> list[tuple[str, str, int | None]]:
    """(symbol, coin_id, market_cap_rank) for every known token."""
    stmt = select(Token.symbol, Token.coin_id, Token.metadata_)
    return [
        (symbol, coin_id or "", (meta or {}).get("market_cap_rank"))
        for symbol, coin_id, meta in (await session.execute(stmt)).all()
    ]
