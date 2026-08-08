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
        restart_delay: float = 5.0,
        sleep: Sleep | None = None,
        normalizer: Normalizer | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._cache = cache
        self._interval = poll_interval
        self._service = service
        self._error_backoff = error_backoff
        self._restart_delay = restart_delay
        self._sleep = sleep or asyncio.sleep
        self._normalizer = normalizer

    async def run_forever(self) -> None:
        """Drive `run()` and bring it back if it ever ends on an exception.

        This is the layer that was missing on 2026-08-07, when one failed Redis
        call escaped `run()` at 23:35 and every HTTP source stopped ingesting
        for twelve hours. Nothing logged it: the collectors hold their tasks in
        `app.state.tasks`, so the strong reference suppressed even asyncio's
        "Task exception was never retrieved", and uvicorn kept answering
        /health with 200 — Docker reported `healthy` for a dead collector.

        `run()` guards its own cycle, so reaching this handler means an
        unforeseen failure above the try. A dead source must be loud and must
        come back, never silent and never gone.
        """
        while True:
            try:
                await self.run()
                return  # `run()` loops forever; returning means a deliberate stop.
            except Exception:
                logger.exception(
                    "%s loop died unexpectedly; restarting in %ss",
                    self._provider.name,
                    self._restart_delay,
                )
                await self._sleep(self._restart_delay)

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
            # Guarded because `Cache.allow` issues a bare `redis.incr` with no
            # error handling of its own, and this call sits above the try that
            # protects the rest of the cycle. One Redis blip here used to end
            # `run()` outright -- see `run_forever`. Unlike `is_enabled`, which
            # is deliberately fail-open, an unknown budget must fail *closed*:
            # polling a rate-limited API on an unverified quota is how a source
            # gets banned rather than throttled.
            try:
                allowed = await self._cache.allow(name, max_calls, window)
            except Exception:
                UPSTREAM_REQUESTS.labels(self._service, name, "error").inc()
                logger.warning(
                    "%s quota check failed; backing off", name, exc_info=True
                )
                await self._sleep(self._error_backoff)
                continue
            if not allowed:
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
