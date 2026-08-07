"""decision-engine entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db import Database
from cmi_common.events.control import ControlCommand, ControlCommandEvent
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .engine import DecisionEngine
from .threshold_job import ThresholdScanJob

logger = logging.getLogger(__name__)

DECISION_THRESHOLD = int(os.getenv("DECISION_THRESHOLD", "70"))

#: Périodicité du scan de calibration. 0 désactive la tâche périodique
#: entièrement (pas seulement un sommeil infini) -- le scan reste
#: déclenchable via RUN_THRESHOLD_SCAN sur `control.commands`.
THRESHOLD_SCAN_INTERVAL_H = float(os.getenv("THRESHOLD_SCAN_INTERVAL_H", "6"))
THRESHOLD_SCAN_DAYS = int(os.getenv("THRESHOLD_SCAN_DAYS", "7"))
THRESHOLD_SCAN_TARGET_PER_DAY = int(os.getenv("THRESHOLD_SCAN_TARGET_PER_DAY", "200"))


async def _threshold_scan_loop(job: ThresholdScanJob, interval_s: float) -> None:
    """Dort d'abord, scanne ensuite : un redémarrage de conteneur ne doit pas
    déclencher un scan coûteux immédiatement. Une exception ne doit jamais
    arrêter la boucle pour le reste de la vie du conteneur -- `run_once` gère
    déjà ses propres erreurs de scan (statut "error" persisté), mais cette
    boucle se protège en plus contre toute exception imprévue (Redis/DB down
    au moment de l'appel)."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await job.run_once()
        except Exception:
            logger.exception("threshold scan loop iteration failed")


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

    cache = Cache(settings.redis)
    db = Database(settings.db)
    job = ThresholdScanJob(
        db,
        cache,
        days=THRESHOLD_SCAN_DAYS,
        target_per_day=THRESHOLD_SCAN_TARGET_PER_DAY,
    )

    async def _control_handle(event: object) -> None:
        if (
            isinstance(event, ControlCommandEvent)
            and event.command == ControlCommand.RUN_THRESHOLD_SCAN
        ):
            await job.run_once()

    # Each replica must apply every command -> unique group per instance, same
    # pattern as trading-engine's control consumer. The Redis lock (not the
    # consumer group) is what guarantees a single scan runs at a time.
    replica = os.getenv("HOSTNAME", "local")
    commands = EventConsumer(
        settings.kafka,
        [Topic.CONTROL],
        _control_handle,
        group_id=f"decision-engine-control-{replica}",
    )
    await commands.start()

    app.state.producer = producer
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())
    app.state.cache = cache
    app.state.db = db
    app.state.threshold_job = job
    app.state.commands = commands
    app.state.commands_task = asyncio.create_task(commands.run())
    app.state.threshold_loop_task = (
        asyncio.create_task(_threshold_scan_loop(job, THRESHOLD_SCAN_INTERVAL_H * 3600))
        if THRESHOLD_SCAN_INTERVAL_H > 0
        else None
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await app.state.commands.stop()
    if app.state.threshold_loop_task is not None:
        app.state.threshold_loop_task.cancel()
    tasks = [
        app.state.consumer_task,
        app.state.commands_task,
        app.state.threshold_loop_task,
    ]
    await asyncio.gather(*[t for t in tasks if t is not None], return_exceptions=True)
    await app.state.producer.stop()
    await app.state.cache.close()
    await app.state.db.dispose()


app = create_app("decision-engine", on_startup=_startup, on_shutdown=_shutdown)
