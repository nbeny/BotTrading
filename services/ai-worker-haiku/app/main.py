"""ai-worker-haiku entrypoint."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.ai import ClaudeClient, CliOptions
from cmi_common.cache import Cache
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .features import FeatureStore
from .worker import HaikuWorker


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    claude = ClaudeClient(
        settings.ai.api_key,
        settings.ai.haiku_model,
        max_tokens=settings.ai.max_tokens,
        transport=settings.ai.transport,
        cli=CliOptions(
            cli_path=settings.ai.cli_path,
            timeout_ms=settings.ai.cli_timeout_ms,
            concurrency=settings.ai.cli_concurrency,
        ),
        cache=cache,
        quota_cooldown_s=settings.ai.quota_cooldown_ms // 1000,
        max_quota_wait_s=settings.ai.max_quota_wait_ms // 1000,
    )
    worker = HaikuWorker(
        claude,
        FeatureStore(cache),
        producer,
        escalation_threshold=settings.ai.escalation_threshold,
    )
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
