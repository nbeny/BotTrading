"""Provider cascade primitives: failover across free-tier data sources.

A ``SourceCascade`` polls an ordered list of ``Provider`` objects (primary
first, unlimited floor last) and publishes events from the first healthy one.
When a provider signals ``RateLimited`` (proactive quota guard) or raises, its
``CircuitBreaker`` is tripped and the cascade falls through to the next
provider, so the pipeline never goes dry. Breakers auto half-open when their
Redis TTL expires, letting the primary resume once its quota window resets.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..cache import Cache
from ..events.base import BaseEvent
from ..kafka import EventProducer, Topic
from ..observability import EVENTS_PRODUCED

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """Raised by a provider that has exhausted its quota for now.

    ``retry_after`` (seconds) hints how long to keep the breaker open; ``None``
    falls back to the cascade/breaker default cooldown.
    """

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


@runtime_checkable
class Provider(Protocol):
    """A single data source. ``name`` keys its breaker; ``fetch`` returns events."""

    name: str

    async def fetch(self) -> list[BaseEvent]:
        ...

    async def close(self) -> None:
        ...


class CircuitBreaker:
    """Redis-backed breaker shared across replicas.

    A tripped provider stays open until its cooldown key TTL expires, after
    which the next poll probes it again (half-open); success keeps it closed,
    another failure re-trips it.
    """

    def __init__(self, cache: Cache, *, default_cooldown: float = 300.0) -> None:
        self._cache = cache
        self._default = default_cooldown

    async def is_open(self, name: str) -> bool:
        return bool(await self._cache.client.exists(f"cb:{name}"))

    async def trip(self, name: str, cooldown: float | None = None) -> None:
        ttl = max(1, int(cooldown if cooldown is not None else self._default))
        await self._cache.client.set(f"cb:{name}", "1", ex=ttl)
