"""Provider cascade primitives: failover across free-tier data sources.

A ``SourceCascade`` polls an ordered list of ``Provider`` objects (primary
first, unlimited floor last) and publishes events from the first healthy one.
When a provider signals ``RateLimitedError`` (proactive quota guard) or raises, its
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


class RateLimitedError(Exception):
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

    async def fetch(self) -> list[BaseEvent]: ...

    async def close(self) -> None: ...


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


class SourceCascade:
    """Polls providers in priority order, serving the first healthy one.

    Primary first, unlimited floor last. Each tick skips providers whose
    breaker is open, tries the rest in order, and publishes the events from
    the first provider that returns without raising. ``RateLimitedError`` trips the
    breaker for its ``retry_after``; any other exception trips it for
    ``error_cooldown``. Both fall through to the next provider.
    """

    def __init__(
        self,
        providers: Sequence[Provider],
        breaker: CircuitBreaker,
        producer: EventProducer,
        topic: Topic,
        *,
        service: str,
        error_cooldown: float = 120.0,
    ) -> None:
        self._providers = list(providers)
        self._breaker = breaker
        self._producer = producer
        self._topic = topic
        self._service = service
        self._error_cooldown = error_cooldown

    async def close(self) -> None:
        for provider in self._providers:
            await provider.close()

    async def poll_once(self) -> int:
        for provider in self._providers:
            if await self._breaker.is_open(provider.name):
                logger.debug("provider %s breaker open; skipping", provider.name)
                continue
            try:
                events = await provider.fetch()
            except RateLimitedError as exc:
                await self._breaker.trip(provider.name, exc.retry_after)
                logger.info("provider %s rate-limited; failing over", provider.name)
                continue
            except Exception:  # any provider failure fails over
                await self._breaker.trip(provider.name, self._error_cooldown)
                logger.warning(
                    "provider %s errored; failing over", provider.name, exc_info=True
                )
                continue
            for event in events:
                await self._producer.publish(self._topic, event)
                EVENTS_PRODUCED.labels(
                    self._service, self._topic.value, event.event_type
                ).inc()
            logger.info("cascade served %d events from %s", len(events), provider.name)
            return len(events)
        logger.warning("all providers exhausted this tick; no events served")
        return 0
