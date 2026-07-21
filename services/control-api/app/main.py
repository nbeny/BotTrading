"""control-api entrypoint: front-facing control plane (JWT-protected)."""
from __future__ import annotations

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db import Database
from cmi_common.kafka import EventProducer

from .commands import CommandPublisher
from .state import StateReader
from .routers import auth as auth_router
from .routers import settings as settings_router


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    publisher = CommandPublisher(producer)
    reader = StateReader(cache, db=db)
    app.state.cache = cache
    app.state.db = db
    app.state.producer = producer
    app.state.settings_service = settings_router.SettingsService(publisher, reader)
    app.state.publisher = publisher
    app.state.reader = reader


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.producer.stop()
    await app.state.cache.close()
    await app.state.db.dispose()


app = create_app("control-api", on_startup=_startup, on_shutdown=_shutdown)
app.include_router(auth_router.router)
app.include_router(settings_router.router)
