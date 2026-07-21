"""collector-reddit entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .collector import RedditCollector

POLL_INTERVAL = float(os.getenv("REDDIT_POLL_INTERVAL", "300"))
SUBREDDITS = os.getenv(
    "REDDIT_SUBREDDITS", "CryptoCurrency,CryptoMoonShots,solana"
).split(",")


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    collector = RedditCollector(
        cache,
        producer,
        subreddits=SUBREDDITS,
        client_id=os.getenv("REDDIT_CLIENT_ID") or None,
        client_secret=os.getenv("REDDIT_CLIENT_SECRET") or None,
    )
    app.state.cache = cache
    app.state.producer = producer
    app.state.collector = collector
    app.state.poller = asyncio.create_task(
        run_periodic(collector.poll_once, POLL_INTERVAL, name="reddit-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.collector.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-reddit", on_startup=_startup, on_shutdown=_shutdown)
