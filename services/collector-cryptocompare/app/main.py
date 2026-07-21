"""collector-cryptocompare entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .collector import CryptoCompareCollector

POLL_INTERVAL = float(os.getenv("CRYPTOCOMPARE_POLL_INTERVAL", "120"))
BASE_URL = os.getenv("CRYPTOCOMPARE_BASE_URL", "https://min-api.cryptocompare.com")
API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or None


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    collector = CryptoCompareCollector(BASE_URL, API_KEY, cache, producer)
    app.state.cache = cache
    app.state.producer = producer
    app.state.collector = collector
    app.state.poller = asyncio.create_task(
        run_periodic(collector.poll_once, POLL_INTERVAL, name="cryptocompare-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.collector.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app(
    "collector-cryptocompare", on_startup=_startup, on_shutdown=_shutdown
)
