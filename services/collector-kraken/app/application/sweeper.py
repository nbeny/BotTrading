"""The two poll loops: 1h over the whole universe, 15m over the majors.

Both share one Kraken quota through Cache.allow(), the same token bucket the
content collectors use. Depth is measured on the broad loop only — the book is
about tradability, which does not need a 5-minute refresh.

The loop owns no database session: everything persistent goes through the store,
which is what lets this be tested against a fake.
"""

from __future__ import annotations

import asyncio
import logging

from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.mapper import parse_depth, parse_ohlc

logger = logging.getLogger(__name__)
SERVICE = "collector-kraken"

_INTERVAL_MINUTES = {"1h": 60, "15m": 15}
_ERROR_BACKOFF = 60.0


class CandleSweeper:
    def __init__(
        self,
        client,
        store,
        cache,
        *,
        interval: str,
        cadence: float,
        majors_only: bool,
        with_depth: bool,
        min_mentions: int,
        sleep=asyncio.sleep,
    ) -> None:
        self._client = client
        self._store = store
        self._cache = cache
        self._interval = interval
        self._cadence = cadence
        self._majors_only = majors_only
        self._with_depth = with_depth
        self._min_mentions = min_mentions
        self._sleep = sleep

    async def _sweep_once(self) -> int:
        max_calls, window = self._client.rate_limit
        minutes = _INTERVAL_MINUTES[self._interval]
        universe, majors = await self._store.resolve(
            await self._client.asset_pairs(), min_mentions=self._min_mentions
        )
        specs = majors if self._majors_only else universe
        stored = 0
        for spec in specs:
            if not await self._cache.allow(self._client.name, max_calls, window):
                await self._sleep(window)
            since = await self._store.last_candle_epoch(spec.symbol, self._interval)
            payload = await self._client.ohlc(
                spec.pair, interval_minutes=minutes, since=since
            )
            stored += await self._store.save_candles(
                parse_ohlc(payload, symbol=spec.symbol, interval=self._interval)
            )
            if self._with_depth:
                snapshot = parse_depth(
                    await self._client.depth(spec.pair), symbol=spec.symbol
                )
                if snapshot is not None:
                    await self._store.save_depth(snapshot)
        return stored

    async def run(self) -> None:
        while True:
            try:
                stored = await self._sweep_once()
            except StopAsyncIteration:
                # The test harness's sleep stub uses this to break the loop.
                raise
            except Exception:
                UPSTREAM_REQUESTS.labels(SERVICE, "kraken", "error").inc()
                logger.warning(
                    "%s sweep failed; backing off", self._interval, exc_info=True
                )
                await self._sleep(_ERROR_BACKOFF)
                continue
            UPSTREAM_REQUESTS.labels(SERVICE, "kraken", "ok").inc()
            # Freshness goes to service_health, which /systems/overview already
            # reads: a dead collector must show up red in the terminal rather
            # than let every reader keep computing on frozen candles.
            await self._store.report_health(interval=self._interval, candles=stored)
            logger.info("%s sweep stored %d candles", self._interval, stored)
            await self._sleep(self._cadence)
