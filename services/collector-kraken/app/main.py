"""collector-kraken: OHLC candles + order-book depth -> Postgres. No Kafka."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database

from .application.store import KrakenStore
from .application.sweeper import CandleSweeper
from .application.universe import DEFAULT_MIN_MENTIONS
from .infrastructure.kraken import KrakenPublicClient

BROAD_CADENCE = float(os.getenv("KRAKEN_BROAD_CADENCE", "900"))
MAJORS_CADENCE = float(os.getenv("KRAKEN_MAJORS_CADENCE", "300"))
MIN_MENTIONS = int(os.getenv("KRAKEN_MAJOR_MIN_MENTIONS_7D", str(DEFAULT_MIN_MENTIONS)))


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    client = KrakenPublicClient()
    store = KrakenStore(db)
    sweepers = [
        CandleSweeper(
            client,
            store,
            cache,
            interval="1h",
            cadence=BROAD_CADENCE,
            majors_only=False,
            with_depth=True,
            min_mentions=MIN_MENTIONS,
        ),
        CandleSweeper(
            client,
            store,
            cache,
            interval="15m",
            cadence=MAJORS_CADENCE,
            majors_only=True,
            with_depth=False,
            min_mentions=MIN_MENTIONS,
        ),
    ]
    app.state.cache = cache
    app.state.db = db
    app.state.client = client
    app.state.tasks = [asyncio.create_task(s.run()) for s in sweepers]


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    await app.state.client.close()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-kraken", on_startup=_startup, on_shutdown=_shutdown)
