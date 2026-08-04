"""ai-worker-haiku entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .features import FeatureStore, MarketRegimeStore
from .scorer import ScorerConfig
from .worker import HaikuWorker


def scorer_config_from_env() -> ScorerConfig:
    """Escalation floor is operator-tunable: it decides how much traffic reaches
    the paid senior analyst, and the right value is only knowable from live
    funnel data. Everything else in ScorerConfig is a model parameter and stays
    fixed — a deploy must not be able to change what the score means.

    Default matches the previous hardcoded value, so enabling this changes
    nothing on its own.
    """
    return ScorerConfig(escalate_score=int(os.getenv("HAIKU_ESCALATE_SCORE", "60")))


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    # Haiku triage is now a deterministic local scorer — no Claude, no quota.
    worker = HaikuWorker(
        FeatureStore(cache),
        producer,
        regime=MarketRegimeStore(cache),
        scorer_config=scorer_config_from_env(),
    )
    consumer = EventConsumer(
        settings.kafka,
        [
            Topic.PRICE,
            Topic.VOLUME,
            Topic.DEX,
            Topic.SENTIMENT,
            Topic.DERIVATIVES,
            Topic.FUNDAMENTALS,
            Topic.DEVELOPER,
        ],
        worker.handle,
        group_id="ai-worker-haiku",
    )
    await consumer.start()
    app.state.cache = cache
    app.state.producer = producer
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())
    # Sweeper: emits one analysis per symbol once its collection window has gone
    # quiet. Without this task the worker would consume events and never score.
    app.state.worker = worker
    app.state.sweep_task = asyncio.create_task(worker.run())


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    app.state.worker.stop()
    await asyncio.gather(
        app.state.consumer_task, app.state.sweep_task, return_exceptions=True
    )
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("ai-worker-haiku", on_startup=_startup, on_shutdown=_shutdown)
