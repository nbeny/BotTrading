"""risk-engine entrypoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .engine import RiskEngine
from .rules import RiskConfig


def _config_from_env() -> RiskConfig:
    return RiskConfig(
        stop_loss_pct=float(os.getenv("RISK_STOP_LOSS_PCT", "0.05")),
        take_profit_pct=float(os.getenv("RISK_TAKE_PROFIT_PCT", "0.10")),
        max_position_pct=float(os.getenv("RISK_MAX_POSITION_PCT", "0.05")),
        min_confidence=float(os.getenv("RISK_MIN_CONFIDENCE", "0.506")),
        min_score=int(os.getenv("RISK_MIN_SCORE", "70")),
        min_risk_reward=float(os.getenv("RISK_MIN_RR", "1.5")),
    )


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    engine = RiskEngine(cache, producer, _config_from_env())
    consumer = EventConsumer(
        settings.kafka,
        [Topic.DECISION],
        engine.handle,
        group_id="risk-engine",
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


app = create_app("risk-engine", on_startup=_startup, on_shutdown=_shutdown)
