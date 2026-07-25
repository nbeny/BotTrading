"""ai-worker-haiku entrypoint."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .features import FeatureStore
from .worker import HaikuWorker


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    # Haiku triage is now a deterministic local scorer — no Claude, no quota.
    worker = HaikuWorker(FeatureStore(cache), producer)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.PRICE, Topic.VOLUME, Topic.DEX, Topic.SENTIMENT],
        worker.handle,
        group_id="ai-worker-haiku",
    )
    await consumer.start()
    app.state.cache = cache
    app.state.producer = producer
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await asyncio.gather(app.state.consumer_task, return_exceptions=True)
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("ai-worker-haiku", on_startup=_startup, on_shutdown=_shutdown)
