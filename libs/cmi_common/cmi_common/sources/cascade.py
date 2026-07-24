"""Rate-limit primitives shared by the ingestion loops.

``RateLimitedError`` signals a provider is throttled; ``CircuitBreaker`` is a
Redis-backed pause gate (a source stays paused until its cooldown TTL expires,
then the loop probes it again). Used by ``AdaptivePollLoop``.
"""

from __future__ import annotations

import logging

from ..cache import Cache

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised by a provider that has exhausted its quota for now.

    ``retry_after`` (seconds) hints how long to keep the source paused; ``None``
    falls back to the loop/breaker default cooldown.
    """

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class CircuitBreaker:
    """Redis-backed pause gate shared across replicas.

    A tripped source stays paused until its cooldown key TTL expires, after
    which the next poll probes it again.
    """

    def __init__(self, cache: Cache, *, default_cooldown: float = 300.0) -> None:
        self._cache = cache
        self._default = default_cooldown

    async def is_open(self, name: str) -> bool:
        return bool(await self._cache.client.exists(f"cb:{name}"))

    async def trip(self, name: str, cooldown: float | None = None) -> None:
        ttl = max(1, int(cooldown if cooldown is not None else self._default))
        await self._cache.client.set(f"cb:{name}", "1", ex=ttl)
