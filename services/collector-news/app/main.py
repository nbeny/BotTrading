"""collector-news: fan-out AdaptivePollLoop per news provider -> raw_content."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.sources import (
    AdaptivePollLoop,
    LexiconLoader,
    LexiconNormalizer,
    Provider,
    RawItem,
    SqlContentRepository,
)

from .providers.cryptocompare import CryptoCompareNewsProvider
from .providers.gdelt import GdeltProvider
from .providers.newsdata import NewsDataProvider
from .providers.rss import RSSProvider

POLL_INTERVAL = float(os.getenv("NEWS_POLL_INTERVAL", "300"))
CC_BASE_URL = os.getenv("CRYPTOCOMPARE_BASE_URL", "https://min-api.cryptocompare.com")
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or None
RSS_FEEDS = [f for f in os.getenv("RSS_FEEDS", "").split(",") if f]
# GDELT indexes all world news, so a bare "cryptocurrency" pulled in football and
# regional reporting that merely brushed the word. The relevance gate rejects
# that downstream, but a tighter query means the rate-limited budget is spent on
# articles that can actually survive it.
DEFAULT_GDELT_QUERY = (
    "(bitcoin OR ethereum OR cryptocurrency OR blockchain OR stablecoin) "
    "sourcelang:english"
)


class _RepoFactory:
    """Session-per-insert repository; the loop only calls ``insert_items``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_items(self, items: list[RawItem]) -> int:
        async with self._db.sessionmaker() as session:
            return await SqlContentRepository(session).insert_items(items)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    repo = _RepoFactory(db)
    providers: list[Provider] = [
        RSSProvider(feeds=RSS_FEEDS or None),
        GdeltProvider(query=os.getenv("GDELT_QUERY", DEFAULT_GDELT_QUERY)),
    ]
    # Key-gated since CryptoCompare was folded into CoinDesk Data: the news
    # endpoint now answers 401 without a key, so running it keyless only burned
    # a request and logged a failure every cycle. Free keys: developers.coindesk.com
    if CC_API_KEY:
        providers.append(CryptoCompareNewsProvider(CC_BASE_URL, CC_API_KEY))
    if os.getenv("NEWSDATA_API_KEY"):
        providers.append(NewsDataProvider(os.getenv("NEWSDATA_API_KEY")))
    normalizer = LexiconNormalizer(
        LexiconLoader(cache, service="collector-news"), service="collector-news"
    )
    loops = [
        # _RepoFactory implements the only method the loop uses (insert_items).
        AdaptivePollLoop(
            p,
            repo,  # type: ignore[arg-type]
            cache,
            poll_interval=POLL_INTERVAL,
            service="collector-news",
            normalizer=normalizer,
        )
        for p in providers
    ]
    app.state.cache = cache
    app.state.db = db
    app.state.loops = loops
    # `run_forever`, not `run`: these tasks are held here for the whole life of
    # the process, so a strong reference suppresses asyncio's "Task exception
    # was never retrieved" and a loop that ends on an exception disappears
    # without a single log line while /health keeps answering 200.
    app.state.tasks = [asyncio.create_task(loop.run_forever()) for loop in loops]


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    for loop in app.state.loops:
        await loop.close()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-news", on_startup=_startup, on_shutdown=_shutdown)
