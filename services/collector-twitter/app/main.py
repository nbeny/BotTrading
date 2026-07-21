"""collector-twitter entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .collector import TwitterCollector

POLL_INTERVAL = float(os.getenv("TWITTER_POLL_INTERVAL") or "300")
# `or` (not getenv default) so an explicitly-empty env var falls back too.
QUERY = (
    os.getenv("TWITTER_QUERY")
    or "(crypto OR cryptocurrency OR altcoin) -is:retweet lang:en"
)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    collector = TwitterCollector(
        cache,
        producer,
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN") or None,
        query=QUERY,
    )
    app.state.cache = cache
    app.state.producer = producer
    app.state.collector = collector
    app.state.poller = asyncio.create_task(
        run_periodic(collector.poll_once, POLL_INTERVAL, name="twitter-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.collector.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-twitter", on_startup=_startup, on_shutdown=_shutdown)
