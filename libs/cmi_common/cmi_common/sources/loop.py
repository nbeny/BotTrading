"""AdaptivePollLoop — runs one provider forever with self-managed rate limits.

Each provider gets its own loop (fan-out; there is no failover). A loop polls,
persists via the repository, and sleeps its normal cadence. When the provider's
proactive budget is spent it waits the rate-limit window; when the provider
raises ``RateLimitedError`` it waits the API-derived ``retry_after`` (or the
provider window) and then resumes the SAME provider.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..cache import Cache
from ..observability import EVENTS_PRODUCED, UPSTREAM_REQUESTS
from .cascade import RateLimitedError
from .provider import Provider
from .raw import RawItem
from .repository import ContentRepository
from .runtime import is_enabled

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class Normalizer(Protocol):
    async def normalize(self, items: list[RawItem]) -> list[RawItem]: ...


class AdaptivePollLoop:
    def __init__(
        self,
        provider: Provider,
        repository: ContentRepository,
        cache: Cache,
        *,
        poll_interval: float,
        service: str,
        error_backoff: float = 120.0,
        sleep: Sleep | None = None,
        normalizer: Normalizer | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._cache = cache
        self._interval = poll_interval
        self._service = service
        self._error_backoff = error_backoff
        self._sleep = sleep or asyncio.sleep
        self._normalizer = normalizer

    async def run(self) -> None:
        max_calls, window = self._provider.rate_limit
        name = self._provider.name
        kind = self._provider.kind
        # Optional capability, deliberately not part of the `Provider` protocol.
        # Most providers re-read a window every cycle and let
        # UNIQUE(source, external_id) absorb the overlap, so they have nothing
        # to confirm. A provider that instead holds a *consumption cursor* has:
        # a cursor is a claim that the data was persisted, and only this loop
        # knows whether it was. `fetch` returning is not that claim — the items
        # still have to survive the normalizer and `insert_items`, and every
        # path out of the `except` blocks below discards them silently.
        commit = getattr(self._provider, "commit", None)
        while True:
            # Operator toggle (collectors:runtime) — skip a cycle when this
            # platform or its whole category is disabled from the UI.
            if not await is_enabled(self._cache, kind, name):
                logger.debug("%s disabled by operator; skipping cycle", name)
                await self._sleep(self._interval)
                continue
            if not await self._cache.allow(name, max_calls, window):
                logger.debug("%s budget spent; waiting %ss", name, window)
                await self._sleep(window)
                continue
            try:
                items = await self._provider.fetch()
                if self._normalizer is not None:
                    # Crypto relevance + symbol resolution, before anything is
                    # stored. One choke point for every provider.
                    items = await self._normalizer.normalize(items)
                # Persist inside the try so a transient DB failure backs off
                # like any other error instead of silently killing this loop.
                inserted = await self._repo.insert_items(items)
                if commit is not None:
                    # The items are durable now, and only now may a cursor say
                    # so. Inside the try for the same reason as the persist:
                    # a failure here backs off rather than killing the loop.
                    await commit()
            except RateLimitedError as exc:
                wait = exc.retry_after if exc.retry_after is not None else window
                UPSTREAM_REQUESTS.labels(self._service, name, "ratelimit").inc()
                logger.info("%s rate-limited; pausing %ss", name, wait)
                await self._sleep(wait)
                continue
            except Exception:
                UPSTREAM_REQUESTS.labels(self._service, name, "error").inc()
                logger.warning("%s cycle failed; backing off", name, exc_info=True)
                await self._sleep(self._error_backoff)
                continue
            UPSTREAM_REQUESTS.labels(self._service, name, "ok").inc()
            EVENTS_PRODUCED.labels(
                self._service, "raw_content", self._provider.kind
            ).inc(inserted)
            logger.info("%s ingested %d new items", name, inserted)
            await self._sleep(self._interval)

    async def close(self) -> None:
        await self._provider.close()
