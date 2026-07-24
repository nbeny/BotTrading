"""collector-social entrypoint: Bluesky -> Reddit cascade on market.social."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer, Topic
from cmi_common.runner import run_periodic
from cmi_common.sources import CircuitBreaker, SourceCascade

from .providers.bluesky import BlueskyProvider
from .providers.reddit import RedditProvider

POLL_INTERVAL = float(os.getenv("SOCIAL_POLL_INTERVAL", "300"))
BLUESKY_QUERY = os.getenv("BLUESKY_QUERY", "crypto")
SUBREDDITS = os.getenv(
    "REDDIT_SUBREDDITS", "CryptoCurrency,CryptoMoonShots,solana"
).split(",")
BREAKER_COOLDOWN = float(os.getenv("SOURCE_BREAKER_COOLDOWN", "300"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    providers = [
        BlueskyProvider(cache, query=BLUESKY_QUERY),
        RedditProvider(
            cache,
            subreddits=SUBREDDITS,
            client_id=os.getenv("REDDIT_CLIENT_ID") or None,
            client_secret=os.getenv("REDDIT_CLIENT_SECRET") or None,
        ),
    ]
    cascade = SourceCascade(
        providers,
        CircuitBreaker(cache, default_cooldown=BREAKER_COOLDOWN),
        producer,
        Topic.SOCIAL,
        service="collector-social",
    )
    app.state.cache = cache
    app.state.producer = producer
    app.state.cascade = cascade
    # NOTE: assumes single-replica deployment. The cascade's is_open->fetch->trip
    # is not atomic across replicas, so multiple replicas would double-poll;
    # cross-replica dedup would need a cache.lock. This service runs single-replica
    # (no replicas: in compose) and the shared cache.allow quota bucket bounds
    # global API usage regardless.
    app.state.poller = asyncio.create_task(
        run_periodic(cascade.poll_once, POLL_INTERVAL, name="social-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.cascade.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-social", on_startup=_startup, on_shutdown=_shutdown)
