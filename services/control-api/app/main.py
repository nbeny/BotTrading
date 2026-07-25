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
from .routers import collectors as collectors_router
from .routers import opportunities as opportunities_router
from .routers import orders as orders_router
from .routers import positions as positions_router
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
    app.state.positions_service = positions_router.PositionsService(publisher, reader)
    app.state.opportunities_service = opportunities_router.OpportunitiesService(publisher, reader)
    app.state.orders_service = orders_router.OrdersService(publisher)
    app.state.publisher = publisher
    app.state.reader = reader


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.producer.stop()
    await app.state.cache.close()
    await app.state.db.dispose()


app = create_app("control-api", on_startup=_startup, on_shutdown=_shutdown)
app.include_router(auth_router.router)
app.include_router(collectors_router.router)
app.include_router(settings_router.router)
app.include_router(positions_router.router)
app.include_router(opportunities_router.router)
app.include_router(orders_router.router)
