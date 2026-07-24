"""Rate-limit primitives shared by the ingestion loops.

``RateLimitedError`` signals a provider is throttled and is handled by
``AdaptivePollLoop`` (which pauses that source in-process until the reset).

``CircuitBreaker`` is a Redis-backed pause gate reserved for cross-replica
pause coordination — it is NOT yet wired into ``AdaptivePollLoop`` (which
currently pauses in-process under the single-replica deployment assumption).
Kept for a future multi-replica setup; do not assume cross-replica coordination
exists today.
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
    """Redis-backed pause gate for future cross-replica coordination.

    A tripped source stays paused until its cooldown key TTL expires. NOT
    wired into ``AdaptivePollLoop`` yet — reserved for a multi-replica setup
    where replicas must share pause state. Today the loop pauses in-process.
    """

    def __init__(self, cache: Cache, *, default_cooldown: float = 300.0) -> None:
        self._cache = cache
        self._default = default_cooldown

    async def is_open(self, name: str) -> bool:
        return bool(await self._cache.client.exists(f"cb:{name}"))

    async def trip(self, name: str, cooldown: float | None = None) -> None:
        ttl = max(1, int(cooldown if cooldown is not None else self._default))
        await self._cache.client.set(f"cb:{name}", "1", ex=ttl)
