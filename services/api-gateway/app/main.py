"""api-gateway entrypoint: REST read API + event persister."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.db import Database
from cmi_common.kafka import EventConsumer, Topic

from . import read_api, routers
from .persister import Persister


async def _startup(app: FastAPI, settings: Settings) -> None:
    db = Database(settings.db)
    persister = Persister(db)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.ANALYSIS, Topic.DECISION, Topic.RISK_APPROVED, Topic.EXECUTION],
        persister.handle,
        group_id="api-gateway-persister",
    )
    await consumer.start()
    app.state.db = db
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())

    # Bind the DB session dependency now that the engine exists.
    app.dependency_overrides[routers.get_session_dep] = db.session


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await asyncio.gather(app.state.consumer_task, return_exceptions=True)
    await app.state.db.dispose()


app = create_app("api-gateway", on_startup=_startup, on_shutdown=_shutdown)
app.include_router(routers.router)
# Live-mode read API backing the web terminal (market + data explorer).
app.include_router(read_api.router)
