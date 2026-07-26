"""api-gateway entrypoint: REST read API + event persister."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.db import Database
from cmi_common.kafka import EventConsumer, Topic

from . import events_api, journal_api, read_api, routers
from .archiver import EventArchiver
from .health_collector import HealthCollector
from .persister import Persister


async def _startup(app: FastAPI, settings: Settings) -> None:
    db = Database(settings.db)
    persister = Persister(db)
    consumer = EventConsumer(
        settings.kafka,
        [
            Topic.PRICE,
            Topic.ANALYSIS,
            Topic.DECISION,
            Topic.RISK_APPROVED,
            Topic.EXECUTION,
            Topic.JOURNAL,
            Topic.ACCOUNT_SNAPSHOT,
        ],
        persister.handle,
        group_id="api-gateway-persister",
    )
    await consumer.start()
    app.state.db = db
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())

    # Raw broadcast archive, so the Command Center feed survives a reload.
    archiver = EventArchiver(db)
    archive_consumer = EventConsumer(
        settings.kafka,
        [
            Topic.PRICE,
            Topic.VOLUME,
            Topic.DEX,
            Topic.SENTIMENT,
            Topic.ANALYSIS,
            Topic.DECISION,
            Topic.RISK_APPROVED,
            Topic.EXECUTION,
            # The archiver routes unrecognised types to events_signal, but that
            # only fires for events it actually receives -- subscription is an
            # explicit list, not a wildcard. Without this line the spec's
            # "events_signal holds ... AccountSnapshot" would never hold.
            Topic.ACCOUNT_SNAPSHOT,
        ],
        archiver.handle,
        # Its own group: the archive must not compete with the persister for
        # partitions, and a lagging archive must not delay business persistence.
        # Topic.JOURNAL is deliberately absent -- the journal has its own table
        # and 180-day retention.
        group_id="api-gateway-archiver",
    )
    await archive_consumer.start()
    app.state.archive_consumer = archive_consumer
    app.state.archive_task = asyncio.create_task(archive_consumer.run())

    # Periodic service-health prober → service_health table (feeds /systems).
    collector = HealthCollector(db)
    app.state.health_collector = collector
    app.state.health_task = asyncio.create_task(collector.run())

    # Bind the DB session dependency now that the engine exists.
    app.dependency_overrides[routers.get_session_dep] = db.session


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await app.state.archive_consumer.stop()
    app.state.health_collector.stop()
    await asyncio.gather(
        app.state.consumer_task,
        app.state.archive_task,
        app.state.health_task,
        return_exceptions=True,
    )
    await app.state.db.dispose()


app = create_app("api-gateway", on_startup=_startup, on_shutdown=_shutdown)
app.include_router(routers.router)
# Live-mode read API backing the web terminal (market + data explorer).
app.include_router(read_api.router)
# Counterfactual-journal summary (own router: read_api is already ~1000 lines).
app.include_router(journal_api.router)
# Archived broadcast stream, so the Command Center feed survives a reload.
app.include_router(events_api.router)
