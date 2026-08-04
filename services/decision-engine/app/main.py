"""decision-engine entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .engine import DecisionEngine

DECISION_THRESHOLD = int(os.getenv("DECISION_THRESHOLD", "70"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    producer = EventProducer(settings.kafka)
    await producer.start()
    engine = DecisionEngine(producer, decision_threshold=DECISION_THRESHOLD)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.ANALYSIS],
        engine.handle,
        group_id="decision-engine",
    )
    await consumer.start()
    app.state.producer = producer
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await asyncio.gather(app.state.consumer_task, return_exceptions=True)
    await app.state.producer.stop()


app = create_app("decision-engine", on_startup=_startup, on_shutdown=_shutdown)
