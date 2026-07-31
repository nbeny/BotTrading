"""collector-defillama service entrypoint (FastAPI + background poller)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from sqlalchemy import select

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.models import Token
from cmi_common.db.session import Database
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .application.collector import DefiLlamaCollector
from .infrastructure.llama_client import LlamaClient

POLL_INTERVAL = float(os.getenv("DEFILLAMA_POLL_INTERVAL", "600"))
MAX_UNLOCK_FETCHES = int(os.getenv("DEFILLAMA_MAX_UNLOCK_FETCHES", "3"))
#: The coin_id -> symbol map changes at the pace of new listings, not of polls.
TOKEN_CACHE_TTL = 900.0


class _TokenMap:
    """Cached CoinGecko id -> symbol lookup, refreshed on a slow timer."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._value: dict[str, str] = {}
        self._loaded_at = 0.0

    async def refresh(self) -> None:
        loop = asyncio.get_running_loop()
        if self._value and loop.time() - self._loaded_at < TOKEN_CACHE_TTL:
            return
        async with self._db.sessionmaker() as session:
            rows = (await session.execute(select(Token.coin_id, Token.symbol))).all()
        self._value = {cid: sym for cid, sym in rows if cid and sym}
        self._loaded_at = loop.time()

    def get(self) -> dict[str, str]:
        return self._value


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = LlamaClient(cache)
    tokens = _TokenMap(db)
    collector = DefiLlamaCollector(
        client,
        producer,
        known_tokens=tokens.get,
        max_unlock_fetches=MAX_UNLOCK_FETCHES,
    )

    async def cycle() -> None:
        await tokens.refresh()
        await collector.poll_once()

    app.state.cache = cache
    app.state.db = db
    app.state.producer = producer
    app.state.client = client
    app.state.poller = asyncio.create_task(
        run_periodic(cycle, POLL_INTERVAL, name="defillama-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.client.close()
    await app.state.producer.stop()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-defillama", on_startup=_startup, on_shutdown=_shutdown)
