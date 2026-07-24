"""collector-news: fan-out AdaptivePollLoop per news provider -> raw_content."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.sources import AdaptivePollLoop, SqlContentRepository

from .providers.cryptocompare import CryptoCompareNewsProvider
from .providers.rss import RSSProvider

POLL_INTERVAL = float(os.getenv("NEWS_POLL_INTERVAL", "300"))
CC_BASE_URL = os.getenv("CRYPTOCOMPARE_BASE_URL", "https://min-api.cryptocompare.com")
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or None
RSS_FEEDS = [f for f in os.getenv("RSS_FEEDS", "").split(",") if f]


class _RepoFactory:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_items(self, items) -> int:
        async with self._db.sessionmaker() as session:
            return await SqlContentRepository(session).insert_items(items)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    repo = _RepoFactory(db)
    providers = [
        CryptoCompareNewsProvider(CC_BASE_URL, CC_API_KEY),
        RSSProvider(feeds=RSS_FEEDS or None),
    ]
    loops = [
        AdaptivePollLoop(
            p, repo, cache, poll_interval=POLL_INTERVAL, service="collector-news"
        )
        for p in providers
    ]
    app.state.cache = cache
    app.state.db = db
    app.state.loops = loops
    app.state.tasks = [asyncio.create_task(loop.run()) for loop in loops]


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    for loop in app.state.loops:
        await loop.close()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-news", on_startup=_startup, on_shutdown=_shutdown)
