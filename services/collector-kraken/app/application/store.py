"""Session-owning facade over persistence and universe resolution.

The sweep loop talks only to this object. Keeping session management out of the
loop is what makes the loop testable with a fake — the same reason
collector-news wraps its repository in a factory.
"""

from __future__ import annotations

from cmi_common.db.session import Database

from ..domain.mapper import DepthSnapshot, OhlcCandle
from ..domain.pairs import VenuePairSpec, parse_asset_pairs
from ..infrastructure.repository import KrakenRepository
from .universe import (
    ambiguous_symbols,
    intersect,
    mention_counts,
    priced_symbols,
    split_regimes,
    token_symbol_ranks,
    untradable,
)


class KrakenStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def resolve(
        self, asset_pairs_payload: dict, *, min_mentions: int
    ) -> tuple[list[VenuePairSpec], list[VenuePairSpec]]:
        """(universe, majors) — and persist the pair reference as a side effect."""
        listed = parse_asset_pairs(asset_pairs_payload)
        async with self._db.sessionmaker() as session:
            priced = await priced_symbols(session)
            specs = intersect(listed, priced)
            majors, _ = split_regimes(
                specs,
                mentions=await mention_counts(session),
                min_mentions=min_mentions,
            )
            repo = KrakenRepository(session)
            await repo.upsert_pairs(
                specs, ambiguous=ambiguous_symbols(await token_symbol_ranks(session))
            )
            await repo.mark_untradable(untradable(listed, priced))
        return specs, majors

    async def last_candle_epoch(self, symbol: str, interval: str) -> int | None:
        async with self._db.sessionmaker() as session:
            return await KrakenRepository(session).last_candle_epoch(symbol, interval)

    async def save_candles(self, candles: list[OhlcCandle]) -> int:
        async with self._db.sessionmaker() as session:
            return await KrakenRepository(session).upsert_candles(candles)

    async def save_depth(self, snapshot: DepthSnapshot) -> int:
        async with self._db.sessionmaker() as session:
            return await KrakenRepository(session).insert_depth([snapshot])

    async def report_health(self, *, interval: str, candles: int) -> None:
        async with self._db.sessionmaker() as session:
            await KrakenRepository(session).report_health(
                interval=interval, candles=candles
            )
