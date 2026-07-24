"""SourceCascade: primary drains first; failover on RateLimitedError/error."""

from __future__ import annotations

from cmi_common.events.base import Source
from cmi_common.events.social import SocialEvent
from cmi_common.kafka import Topic
from cmi_common.sources import CircuitBreaker, RateLimitedError, SourceCascade


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.keys else 0

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.keys[key] = value


class FakeCache:
    def __init__(self) -> None:
        self._redis = FakeRedis()

    @property
    def client(self) -> FakeRedis:
        return self._redis


class FakeProducer:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, _topic, event) -> None:
        self.published.append(event)


def _event(symbol: str) -> SocialEvent:
    return SocialEvent(source=Source.BLUESKY, symbol=symbol, mentions=1)


class StubProvider:
    def __init__(self, name: str, *, events=None, raises: Exception | None = None) -> None:
        self.name = name
        self._events = events or []
        self._raises = raises
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._events

    async def close(self) -> None:
        pass


async def test_primary_serves_and_fallback_untouched() -> None:
    primary = StubProvider("bluesky", events=[_event("BTC")])
    fallback = StubProvider("reddit", events=[_event("ETH")])
    producer = FakeProducer()
    cascade = SourceCascade(
        [primary, fallback], CircuitBreaker(FakeCache()), producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert published == 1
    assert [e.symbol for e in producer.published] == ["BTC"]
    assert fallback.calls == 0  # primary healthy -> fallback never polled


async def test_rate_limited_primary_trips_and_falls_through() -> None:
    primary = StubProvider("bluesky", raises=RateLimitedError(30.0))
    fallback = StubProvider("reddit", events=[_event("ETH")])
    producer = FakeProducer()
    breaker = CircuitBreaker(FakeCache())
    cascade = SourceCascade(
        [primary, fallback], breaker, producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert published == 1
    assert [e.symbol for e in producer.published] == ["ETH"]
    assert await breaker.is_open("bluesky") is True


async def test_open_breaker_skips_provider_without_calling() -> None:
    primary = StubProvider("bluesky", events=[_event("BTC")])
    fallback = StubProvider("reddit", events=[_event("ETH")])
    producer = FakeProducer()
    breaker = CircuitBreaker(FakeCache())
    await breaker.trip("bluesky", 300.0)
    cascade = SourceCascade(
        [primary, fallback], breaker, producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert primary.calls == 0  # breaker open -> skipped entirely
    assert [e.symbol for e in producer.published] == ["ETH"]
    assert published == 1


async def test_all_exhausted_returns_zero() -> None:
    primary = StubProvider("bluesky", raises=RateLimitedError())
    fallback = StubProvider("reddit", raises=RuntimeError("boom"))
    producer = FakeProducer()
    cascade = SourceCascade(
        [primary, fallback], CircuitBreaker(FakeCache()), producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert published == 0
    assert producer.published == []
