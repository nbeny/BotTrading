"""sentiment-service: DB-sourced scoring worker (publishes SentimentEvent)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.db.session import Database
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic
from cmi_common.sources import SqlContentRepository

from .scorer import SentimentScorer
from .worker import SentimentDbWorker

MODEL_NAME = os.getenv("SENTIMENT_MODEL", "ElKulako/cryptobert")
WORKER_INTERVAL = float(os.getenv("SENTIMENT_WORKER_INTERVAL", "10"))
BATCH = int(os.getenv("SENTIMENT_BATCH", "100"))
COMPACT_INTERVAL = float(os.getenv("SENTIMENT_COMPACT_INTERVAL", "21600"))  # 6h
HOURLY_RETENTION_DAYS = int(os.getenv("SENTIMENT_HOURLY_RETENTION_DAYS", "35"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    producer = EventProducer(settings.kafka)
    await producer.start()
    db = Database(settings.db)
    scorer = SentimentScorer(MODEL_NAME)

    async def _tick() -> None:
        async with db.sessionmaker() as session:
            worker = SentimentDbWorker(
                SqlContentRepository(session), scorer, producer, batch=BATCH
            )
            await worker.run_once()

    async def _compact() -> None:
        async with db.sessionmaker() as session:
            cutoff = datetime.now(tz=UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=HOURLY_RETENTION_DAYS)
            n = await SqlContentRepository(session).compact_hourly_to_daily(
                older_than=cutoff
            )
            if n:
                logging.getLogger("sentiment-service").info(
                    "compacted %d hourly buckets into daily", n
                )

    app.state.producer = producer
    app.state.db = db
    app.state.worker_task = asyncio.create_task(
        run_periodic(_tick, WORKER_INTERVAL, name="sentiment-worker")
    )
    # critical=False: une compaction ratee merite une metrique, pas de faire
    # repondre /health 503 alors que sentiment-worker (10 s) scorerait
    # parfaitement.
    app.state.compact_task = asyncio.create_task(
        run_periodic(
            _compact, COMPACT_INTERVAL, name="sentiment-compaction", critical=False
        )
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.worker_task.cancel()
    app.state.compact_task.cancel()
    await asyncio.gather(
        app.state.worker_task, app.state.compact_task, return_exceptions=True
    )
    await app.state.db.dispose()
    await app.state.producer.stop()


app = create_app("sentiment-service", on_startup=_startup, on_shutdown=_shutdown)
