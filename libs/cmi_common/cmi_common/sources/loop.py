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

from ..cache import Cache
from ..observability import EVENTS_PRODUCED, UPSTREAM_REQUESTS
from .cascade import RateLimitedError
from .provider import Provider
from .repository import ContentRepository

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


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
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._cache = cache
        self._interval = poll_interval
        self._service = service
        self._error_backoff = error_backoff
        self._sleep = sleep or asyncio.sleep

    async def run(self) -> None:
        max_calls, window = self._provider.rate_limit
        name = self._provider.name
        while True:
            if not await self._cache.allow(name, max_calls, window):
                logger.debug("%s budget spent; waiting %ss", name, window)
                await self._sleep(window)
                continue
            try:
                items = await self._provider.fetch()
            except RateLimitedError as exc:
                wait = exc.retry_after if exc.retry_after is not None else window
                UPSTREAM_REQUESTS.labels(self._service, name, "ratelimit").inc()
                logger.info("%s rate-limited; pausing %ss", name, wait)
                await self._sleep(wait)
                continue
            except Exception:  # noqa: BLE001 - one bad poll never kills the loop
                UPSTREAM_REQUESTS.labels(self._service, name, "error").inc()
                logger.warning("%s poll failed; backing off", name, exc_info=True)
                await self._sleep(self._error_backoff)
                continue
            inserted = await self._repo.insert_items(items)
            UPSTREAM_REQUESTS.labels(self._service, name, "ok").inc()
            EVENTS_PRODUCED.labels(self._service, "raw_content", self._provider.kind).inc(
                inserted
            )
            logger.info("%s ingested %d new items", name, inserted)
            await self._sleep(self._interval)

    async def close(self) -> None:
        await self._provider.close()
