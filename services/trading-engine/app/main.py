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

    from .runtime import RuntimeConfig
    from .control import ControlHandler
    from cmi_common.events.control import ControlCommandEvent  # noqa: F401 (topic import)

    await RuntimeConfig.write_defaults_if_absent(cache, config)

    # Cache the latest mode so the Kraken client resolves it cheaply per call.
    app.state.mode = config.mode

    async def _current_mode():
        eff = await RuntimeConfig.load(cache, config)
        app.state.mode = eff.mode
        return eff.mode

    kraken = KrakenFuturesClient(config, mode_provider=lambda: app.state.mode)
    await kraken.start()

    # Adopt any operator-persisted runtime mode at boot (not just after the first
    # control command), so the Kraken client routes correctly from the start.
    await _current_mode()

    engine = TradingEngine(cache, producer, kraken, config)
    signals = EventConsumer(
        settings.kafka, [Topic.RISK_APPROVED], engine.handle, group_id="trading-engine",
    )
    await signals.start()

    control = ControlHandler(cache, engine=engine, kraken=kraken, defaults=config)

    async def _control_handle(event):
        await control.handle(event)
        await _current_mode()  # refresh cached mode after any settings command

    # Each engine replica must apply every command -> unique group per instance.
    import os
    replica = os.getenv("HOSTNAME", "local")
    commands = EventConsumer(
        settings.kafka, [Topic.CONTROL], _control_handle,
        group_id=f"trading-engine-control-{replica}",
    )
    await commands.start()

    reconciler = Reconciler(cache, producer, kraken)
    await reconciler.sweep()

    app.state.cache = cache
    app.state.producer = producer
    app.state.kraken = kraken
    app.state.signals = signals
    app.state.commands = commands
    app.state.reconciler = reconciler
    app.state.signals_task = asyncio.create_task(signals.run())
    app.state.commands_task = asyncio.create_task(commands.run())
    app.state.reconcile_task = asyncio.create_task(reconciler.run(config.reconcile_interval_s))


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.reconciler.stop()
    await app.state.signals.stop()
    await app.state.commands.stop()
    await asyncio.gather(
        app.state.signals_task, app.state.commands_task, app.state.reconcile_task,
        return_exceptions=True,
    )
    await app.state.kraken.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("trading-engine", on_startup=_startup, on_shutdown=_shutdown)
