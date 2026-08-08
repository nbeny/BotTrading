"""collector-social: fan-out AdaptivePollLoop per social provider -> raw_content."""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.sources import (
    RUNTIME_KEY,
    AdaptivePollLoop,
    LexiconLoader,
    LexiconNormalizer,
    Provider,
    RawItem,
    SqlContentRepository,
    parse_channels,
    set_runtime,
)

from .providers.bluesky import BlueskyProvider
from .providers.fourchan import FourchanProvider
from .providers.lens import LensProvider
from .providers.mastodon import MastodonProvider
from .providers.neynar import NeynarProvider
from .providers.reddit import RedditProvider
from .providers.telegram import TelegramProvider
from .providers.youtube import YouTubeProvider

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.getenv("SOCIAL_POLL_INTERVAL", "300"))
SUBREDDITS = os.getenv(
    "REDDIT_SUBREDDITS", "CryptoCurrency,CryptoMoonShots,solana"
).split(",")


def _build_providers(cache: Cache) -> list[Provider]:
    providers: list[Provider] = [
        BlueskyProvider(
            query=os.getenv("BLUESKY_QUERY", "crypto"),
            identifier=os.getenv("BLUESKY_IDENTIFIER") or None,
            app_password=os.getenv("BLUESKY_APP_PASSWORD") or None,
        )
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
    if os.getenv("NEYNAR_API_KEY"):
        providers.append(NeynarProvider(os.getenv("NEYNAR_API_KEY")))
    if os.getenv("YOUTUBE_API_KEY"):
        providers.append(YouTubeProvider(os.getenv("YOUTUBE_API_KEY")))
    providers.append(LensProvider(query=os.getenv("LENS_QUERY", "crypto")))
    telegram = _telegram_provider(cache)
    if telegram is not None:
        providers.append(telegram)
    return providers


def _telegram_provider(cache: Cache) -> TelegramProvider | None:
    """Telegram runs on a user session, so all three credentials must be present.

    The session is minted out-of-band by ``scripts/telegram_session.py``; there
    is no way to log in from here, so a partial config disables the source
    rather than starting a provider that can only ever fail.
    """
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session = os.getenv("TELEGRAM_SESSION", "").strip()
    if not (api_id.isdigit() and api_hash and session):
        return None
    return TelegramProvider(
        api_id=int(api_id),
        api_hash=api_hash,
        session=session,
        cache=cache,
    )


async def _seed_telegram_channels(cache: Cache) -> None:
    """Write ``TELEGRAM_CHANNELS`` into ``collectors:runtime`` on first boot only.

    Once the key carries a list the operator owns it, so re-applying the env var
    on every restart would silently undo every edit made from the terminal. An
    absent entry is the only "never configured" signal: ``[]`` is a deliberate
    "poll nobody" and must survive a restart untouched.
    """
    cfg = await cache.get_json(RUNTIME_KEY) or {}
    if cfg.get("telegram_channels") is not None:
        return
    await set_runtime(
        cache, {"telegram_channels": parse_channels(os.getenv("TELEGRAM_CHANNELS"))}
    )


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
    try:
        await _seed_telegram_channels(cache)
    except Exception as exc:
        # Best effort: a seed that cannot be written must not keep the other
        # seven collectors from starting. The provider re-reads the key every
        # cycle anyway, so a later restart still gets its chance to seed.
        logger.warning("telegram: could not seed the channel list — %s", exc)
    providers = _build_providers(cache)
    normalizer = LexiconNormalizer(
        LexiconLoader(cache, service="collector-social"), service="collector-social"
    )
    loops = [
        # _RepoFactory implements the only method the loop uses (insert_items).
        AdaptivePollLoop(
            p,
            repo,  # type: ignore[arg-type]
            cache,
            poll_interval=POLL_INTERVAL,
            service="collector-social",
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


app = create_app("collector-social", on_startup=_startup, on_shutdown=_shutdown)
