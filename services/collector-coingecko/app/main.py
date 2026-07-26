"""collector-coingecko service entrypoint (FastAPI + background poller)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .application.collector import CoinGeckoCollector
from .infrastructure.coingecko_client import CoinGeckoClient

POLL_INTERVAL = float(os.getenv("COINGECKO_POLL_INTERVAL", "60"))
BASE_URL = os.getenv("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
API_KEY = os.getenv("COINGECKO_API_KEY") or None


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = CoinGeckoClient(BASE_URL, API_KEY, cache)
    collector = CoinGeckoCollector(client, producer, cache=cache)

    app.state.cache = cache
    app.state.producer = producer
    app.state.client = client
    app.state.poller = asyncio.create_task(
        run_periodic(collector.poll_once, POLL_INTERVAL, name="coingecko-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.client.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-coingecko", on_startup=_startup, on_shutdown=_shutdown)
