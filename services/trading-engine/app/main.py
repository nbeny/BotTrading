# services/trading-engine/app/main.py
"""trading-engine entrypoint."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .config import TradingConfig
from .engine import TradingEngine
from .kraken import KrakenFuturesClient
from .reconcile import Reconciler


async def _startup(app: FastAPI, settings: Settings) -> None:
    config = TradingConfig.from_env()
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    kraken = KrakenFuturesClient(config)
    await kraken.start()

    engine = TradingEngine(cache, producer, kraken, config)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.RISK_APPROVED],
        engine.handle,
        group_id="trading-engine",
    )
    await consumer.start()

    reconciler = Reconciler(cache, producer, kraken)
    await reconciler.sweep()  # resync at boot

    app.state.cache = cache
    app.state.producer = producer
    app.state.kraken = kraken
    app.state.consumer = consumer
    app.state.reconciler = reconciler
    app.state.consumer_task = asyncio.create_task(consumer.run())
    app.state.reconcile_task = asyncio.create_task(
        reconciler.run(config.reconcile_interval_s)
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.reconciler.stop()
    await app.state.consumer.stop()
    await asyncio.gather(
        app.state.consumer_task, app.state.reconcile_task, return_exceptions=True
    )
    await app.state.kraken.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("trading-engine", on_startup=_startup, on_shutdown=_shutdown)
