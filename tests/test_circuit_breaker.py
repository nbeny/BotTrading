"""CircuitBreaker: trip opens a provider until its cooldown TTL expires."""

from __future__ import annotations

from cmi_common.sources import CircuitBreaker


class FakeRedis:
    """Minimal async redis stub tracking set-with-ttl and existence."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.keys else 0

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.keys[key] = value
        if ex is not None:
            self.ttls[key] = ex


class FakeCache:
    def __init__(self) -> None:
        self._redis = FakeRedis()

    @property
    def client(self) -> FakeRedis:
        return self._redis


async def test_closed_by_default() -> None:
    breaker = CircuitBreaker(FakeCache())
    assert await breaker.is_open("bluesky") is False


async def test_trip_opens_with_retry_after_ttl() -> None:
    cache = FakeCache()
    breaker = CircuitBreaker(cache, default_cooldown=300.0)

    await breaker.trip("bluesky", 42.0)

    assert await breaker.is_open("bluesky") is True
    assert cache.client.ttls["cb:bluesky"] == 42


async def test_trip_without_retry_after_uses_default() -> None:
    cache = FakeCache()
    breaker = CircuitBreaker(cache, default_cooldown=300.0)

    await breaker.trip("reddit", None)

    assert cache.client.ttls["cb:reddit"] == 300
