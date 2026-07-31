"""Read-side access to `candles` and `market_depth`.

Mirrors SqlSentimentAggReader: the interval/closedness maths are pure functions
tested without a database, and the class only runs range queries.

This module deliberately computes no indicator. RSI, EMA and ATR belong to the
scoring layer; putting them here would make the collector's storage format and
the strategy's maths impossible to change independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Candle as CandleRow
from ..db.models import MarketDepth

INTERVALS: tuple[str, ...] = ("15m", "1h")

_INTERVAL_DELTAS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
}


@dataclass(slots=True)
class Candle:
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(slots=True)
class Depth:
    time: datetime
    mid_price: Decimal
    spread_pct: float
    bid_depth_usd: Decimal
    ask_depth_usd: Decimal

    @property
    def total_depth_usd(self) -> Decimal:
        return self.bid_depth_usd + self.ask_depth_usd


def interval_delta(interval: str) -> timedelta:
    try:
        return _INTERVAL_DELTAS[interval]
    except KeyError as exc:
        raise ValueError(f"unknown interval {interval!r}") from exc


def is_closed(bucket_start: datetime, interval: str, now: datetime) -> bool:
    """True once the whole bucket has elapsed.

    Derived rather than stored: an indicator computed over a still-forming
    candle silently reads a partial bar, and a `closed` column would only be as
    honest as the last writer to remember it.
    """
    return bucket_start + interval_delta(interval) <= now


class SqlCandleReader:
    """AsyncSession-backed reader over candles / market_depth."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def series(
        self,
        *,
        symbol: str,
        interval: str,
        points: int,
        closed_only: bool = True,
        now: datetime | None = None,
    ) -> list[Candle]:
        """Up to `points` candles, oldest first, current bucket excluded by default."""
        now = now or datetime.now(tz=UTC)
        delta = interval_delta(interval)
        since = now - delta * points
        stmt = (
            select(CandleRow)
            .where(CandleRow.symbol == symbol)
            .where(CandleRow.interval == interval)
            .where(CandleRow.time >= since)
            .order_by(CandleRow.time.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            Candle(
                time=r.time,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
            )
            for r in rows
            if not closed_only or is_closed(r.time, interval, now)
        ][-points:]

    async def latest(
        self,
        *,
        symbols: list[str],
        interval: str,
        closed_only: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Candle]:
        """Most recent candle per symbol, in one query."""
        now = now or datetime.now(tz=UTC)
        if not symbols:
            return {}
        stmt = (
            select(CandleRow)
            .where(CandleRow.symbol.in_(symbols))
            .where(CandleRow.interval == interval)
            .distinct(CandleRow.symbol)
            .order_by(CandleRow.symbol, CandleRow.time.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            r.symbol: Candle(
                time=r.time,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
            )
            for r in rows
            if not closed_only or is_closed(r.time, interval, now)
        }

    async def latest_depth(self, *, symbols: list[str]) -> dict[str, Depth]:
        """Most recent book snapshot per symbol, in one query.

        A symbol absent from the result was never measured. Callers must keep
        that distinct from "measured and thin" — an unmeasured symbol coerced to
        0.0 would read downstream as a dead market.
        """
        if not symbols:
            return {}
        stmt = (
            select(MarketDepth)
            .where(MarketDepth.symbol.in_(symbols))
            .distinct(MarketDepth.symbol)
            .order_by(MarketDepth.symbol, MarketDepth.time.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            r.symbol: Depth(
                time=r.time,
                mid_price=r.mid_price,
                spread_pct=r.spread_pct,
                bid_depth_usd=r.bid_depth_usd,
                ask_depth_usd=r.ask_depth_usd,
            )
            for r in rows
        }
