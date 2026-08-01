"""collector-binance-futures entrypoint (FastAPI + background poller)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.db.universe import (
    DEFAULT_MIN_MENTIONS,
    majors,
    mention_counts,
    priced_symbols,
)
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .application.collector import BinanceFuturesCollector
from .infrastructure.binance_client import BinanceFuturesClient

POLL_INTERVAL = float(os.getenv("BINANCE_FUTURES_POLL_INTERVAL", "300"))
#: Shared with collector-kraken on purpose — one definition of "major", so the
#: two collectors cannot drift on which symbols justify per-symbol API budget.
MIN_MENTIONS = int(os.getenv("KRAKEN_MAJOR_MIN_MENTIONS_7D", str(DEFAULT_MIN_MENTIONS)))
#: The universe moves at the pace of listings and mention counts, not of polls.
UNIVERSE_TTL = 900.0


class _Universe:
    """Cached (priced, majors, ambiguous) triple, refreshed on a slow timer."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._value: tuple[set[str], set[str], set[str]] = (set(), set(), set())
        self._loaded_at = 0.0

    async def refresh(self) -> None:
        loop = asyncio.get_running_loop()
        # Gating on a non-empty value is load-bearing, not an optimisation: an
        # empty result is never cached, so a cold start or a transient DB
        # failure self-heals on the next cycle instead of freezing an empty
        # universe in place for 900s.
        if self._value[0] and loop.time() - self._loaded_at < UNIVERSE_TTL:
            return
        async with self._db.sessionmaker() as session:
            priced = await priced_symbols(session)
            mentions = await mention_counts(session)
        # Ambiguity is collector-kraken's venue-pair concern and nothing here
        # can resolve a ticker collision, so the set stays empty until a shared
        # source exists. An empty set means "no known collisions", not "we did
        # not look" — the mapper skips ambiguous tickers rather than arbitrating.
        self._value = (
            priced,
            majors(priced, mentions, min_mentions=MIN_MENTIONS),
            set(),
        )
        self._loaded_at = loop.time()

    def get(self) -> tuple[set[str], set[str], set[str]]:
        return self._value


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = BinanceFuturesClient(cache)
    universe = _Universe(db)
    collector = BinanceFuturesCollector(client, producer, universe=universe.get)

    async def cycle() -> None:
        await universe.refresh()
        await collector.poll_once()

    app.state.cache = cache
    app.state.db = db
    app.state.producer = producer
    app.state.client = client
    app.state.poller = asyncio.create_task(
        run_periodic(cycle, POLL_INTERVAL, name="binance-futures-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.client.close()
    await app.state.producer.stop()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app(
    "collector-binance-futures", on_startup=_startup, on_shutdown=_shutdown
)
