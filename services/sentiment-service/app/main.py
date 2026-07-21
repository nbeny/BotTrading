"""sentiment-service entrypoint (FastAPI + Kafka consumer)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .handler import SentimentHandler
from .scorer import SentimentScorer

MODEL_NAME = os.getenv("SENTIMENT_MODEL", "ElKulako/cryptobert")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "sentiment-service")


async def _startup(app: FastAPI, settings: Settings) -> None:
    producer = EventProducer(settings.kafka)
    await producer.start()
    scorer = SentimentScorer(MODEL_NAME)
    handler = SentimentHandler(scorer, producer)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.NEWS, Topic.SOCIAL],
        handler.handle,
        group_id=GROUP_ID,
    )
    await consumer.start()
    app.state.producer = producer
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await asyncio.gather(app.state.consumer_task, return_exceptions=True)
    await app.state.producer.stop()


app = create_app("sentiment-service", on_startup=_startup, on_shutdown=_shutdown)
