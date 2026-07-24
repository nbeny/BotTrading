"""collector-social: fan-out AdaptivePollLoop per social provider -> raw_content."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.sources import (
    AdaptivePollLoop,
    Provider,
    RawItem,
    SqlContentRepository,
)

from .providers.bluesky import BlueskyProvider
from .providers.fourchan import FourchanProvider
from .providers.mastodon import MastodonProvider
from .providers.reddit import RedditProvider

POLL_INTERVAL = float(os.getenv("SOCIAL_POLL_INTERVAL", "300"))
SUBREDDITS = os.getenv(
    "REDDIT_SUBREDDITS", "CryptoCurrency,CryptoMoonShots,solana"
).split(",")


def _build_providers() -> list[Provider]:
    providers: list[Provider] = [
        BlueskyProvider(query=os.getenv("BLUESKY_QUERY", "crypto"))
    ]
    providers.append(
        RedditProvider(
            subreddits=SUBREDDITS,
            client_id=os.getenv("REDDIT_CLIENT_ID") or None,
            client_secret=os.getenv("REDDIT_CLIENT_SECRET") or None,
        )
    )
    providers.append(
        MastodonProvider(
            instance=os.getenv("MASTODON_INSTANCE", "mastodon.social"),
            hashtag=os.getenv("MASTODON_HASHTAG", "crypto"),
        )
    )
    providers.append(FourchanProvider())
    return providers


class _RepoFactory:
    """Yields a fresh SqlContentRepository bound to a new session per insert.

    The loop only ever calls ``insert_items``; a short-lived session per poll
    keeps long-running loops from holding a pooled connection idle.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_items(self, items: list[RawItem]) -> int:
        async with self._db.sessionmaker() as session:
            return await SqlContentRepository(session).insert_items(items)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    repo = _RepoFactory(db)
    providers = _build_providers()
    loops = [
        # _RepoFactory implements the only method the loop uses (insert_items).
        AdaptivePollLoop(
            p,
            repo,  # type: ignore[arg-type]
            cache,
            poll_interval=POLL_INTERVAL,
            service="collector-social",
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


app = create_app("collector-social", on_startup=_startup, on_shutdown=_shutdown)
