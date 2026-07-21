"""collector-dexscreener entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .collector import DexScreenerCollector

POLL_INTERVAL = float(os.getenv("DEXSCREENER_POLL_INTERVAL", "30"))
BASE_URL = os.getenv("DEXSCREENER_BASE_URL", "https://api.dexscreener.com")


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    collector = DexScreenerCollector(BASE_URL, cache, producer)
    app.state.cache = cache
    app.state.producer = producer
    app.state.collector = collector
    app.state.poller = asyncio.create_task(
        run_periodic(collector.poll_once, POLL_INTERVAL, name="dexscreener-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.collector.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-dexscreener", on_startup=_startup, on_shutdown=_shutdown)
