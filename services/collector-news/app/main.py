"""collector-news entrypoint: CryptoCompare -> RSS cascade on market.news."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer, Topic
from cmi_common.runner import run_periodic
from cmi_common.sources import CircuitBreaker, SourceCascade

from .providers.cryptocompare import CryptoCompareNewsProvider
from .providers.rss import RSSProvider

POLL_INTERVAL = float(os.getenv("NEWS_POLL_INTERVAL", "300"))
CC_BASE_URL = os.getenv("CRYPTOCOMPARE_BASE_URL", "https://min-api.cryptocompare.com")
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or None
RSS_FEEDS = [f for f in os.getenv("RSS_FEEDS", "").split(",") if f]
BREAKER_COOLDOWN = float(os.getenv("SOURCE_BREAKER_COOLDOWN", "300"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    providers = [
        CryptoCompareNewsProvider(CC_BASE_URL, CC_API_KEY, cache),
        RSSProvider(cache, feeds=RSS_FEEDS or None, source_name="RSS"),
    ]
    cascade = SourceCascade(
        providers,
        CircuitBreaker(cache, default_cooldown=BREAKER_COOLDOWN),
        producer,
        Topic.NEWS,
        service="collector-news",
    )
    app.state.cache = cache
    app.state.producer = producer
    app.state.cascade = cascade
    # Poller assumes single-replica deployment: cross-replica dedup would need
    # cache.lock, but the shared cache.allow quota bucket already bounds global
    # API usage, so no distributed lock is required here.
    app.state.poller = asyncio.create_task(
        run_periodic(cascade.poll_once, POLL_INTERVAL, name="news-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.cascade.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-news", on_startup=_startup, on_shutdown=_shutdown)
