"""Upserts for depth snapshots and the venue pair reference, plus the
``last_candle_epoch`` cursor read (candles themselves are now published on
Kafka and upserted by api-gateway's persister -- see ``application/sweeper.py``)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db.models import Candle, MarketDepth, ServiceHealth, VenuePair

from ..domain.mapper import DepthSnapshot
from ..domain.pairs import VenuePairSpec

VENUE = "kraken"
SERVICE = "collector-kraken"


class KrakenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_depth(self, snapshots: list[DepthSnapshot]) -> int:
        if not snapshots:
            return 0
        now = datetime.now(tz=UTC)
        stmt = (
            insert(MarketDepth)
            .values(
                [
                    {
                        "time": now,
                        "symbol": s.symbol,
                        "mid_price": s.mid_price,
                        "spread_pct": s.spread_pct,
                        "bid_depth_usd": s.bid_depth_usd,
                        "ask_depth_usd": s.ask_depth_usd,
                        "source": VENUE,
                    }
                    for s in snapshots
                ]
            )
            .on_conflict_do_nothing(index_elements=["time", "symbol"])
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return len(snapshots)

    async def upsert_pairs(
        self, specs: list[VenuePairSpec], *, ambiguous: set[str]
    ) -> int:
        if not specs:
            return 0
        now = datetime.now(tz=UTC)
        stmt = insert(VenuePair).values(
            [
                {
                    "venue": VENUE,
                    "symbol": s.symbol,
                    "pair": s.pair,
                    "ordermin": s.ordermin,
                    "tradable": True,
                    "ambiguous": s.symbol in ambiguous,
                    "updated_at": now,
                }
                for s in specs
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["venue", "symbol"],
            set_={
                "pair": stmt.excluded.pair,
                "ordermin": stmt.excluded.ordermin,
                "tradable": stmt.excluded.tradable,
                "ambiguous": stmt.excluded.ambiguous,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return len(specs)

    async def mark_untradable(self, symbols: set[str]) -> int:
        """Record symbols we price but the venue does not list.

        Stored once as a fact rather than re-derived (and re-logged) every cycle:
        "no Kraken pair" is the answer to "why does this symbol never signal?",
        and a later lot reads it as the tradability filter.
        """
        if not symbols:
            return 0
        now = datetime.now(tz=UTC)
        stmt = insert(VenuePair).values(
            [
                {
                    "venue": VENUE,
                    "symbol": s,
                    "pair": "",
                    "ordermin": None,
                    "tradable": False,
                    "ambiguous": False,
                    "updated_at": now,
                }
                for s in sorted(symbols)
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["venue", "symbol"],
            set_={
                "tradable": stmt.excluded.tradable,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return len(symbols)

    async def report_health(self, *, interval: str, candles: int) -> None:
        """Publish sweep freshness to service_health, which /systems/overview reads.

        The two sweepers share one row, so `detail` is merged with the JSONB `||`
        operator rather than replaced — otherwise each loop would erase the
        other's timestamp and both would look stale half the time.
        """
        now = datetime.now(tz=UTC)
        detail = {
            f"last_sweep_{interval}": now.isoformat(),
            f"candles_{interval}": candles,
        }
        stmt = insert(ServiceHealth).values(
            service=SERVICE,
            status="healthy",
            healthy=True,
            latency_ms=0.0,
            detail=detail,
            checked_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["service"],
            set_={
                "status": stmt.excluded.status,
                "healthy": stmt.excluded.healthy,
                "checked_at": stmt.excluded.checked_at,
                "detail": ServiceHealth.detail.op("||")(stmt.excluded.detail),
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def last_candle_epoch(self, symbol: str, interval: str) -> int | None:
        """Unix seconds of the newest stored candle — the OHLC `since` cursor."""
        stmt = (
            select(Candle.time)
            .where(Candle.symbol == symbol, Candle.interval == interval)
            .order_by(Candle.time.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return int(row.timestamp()) if row else None
