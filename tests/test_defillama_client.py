"""The client's caching and failure behaviour, without touching the network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from service_modules import load_service_module

client_mod = load_service_module("collector-defillama", "infrastructure.llama_client")


class FakeCache:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = dict(initial or {})
        self.writes: list[tuple[str, Any, int]] = []

    async def get_json(self, key: str) -> Any | None:
        return self.data.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.data[key] = value
        self.writes.append((key, value, ttl_seconds))

    async def allow(self, key: str, limit: int, window: int) -> bool:
        return True


def _client(handler, cache: FakeCache):
    transport = httpx.MockTransport(handler)
    return client_mod.LlamaClient(cache, transport=transport)


async def test_cached_unlock_is_served_without_a_request() -> None:
    # The whole point of the 24h cache: a 2.25 MB body must not be refetched.
    cache = FakeCache(
        {"defillama:unlock:aave": {"at": "2026-08-10T00:00:00+00:00", "pct_supply": 2.5}}
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    unlock = await _client(handler, cache).unlock("aave", "aave")
    assert calls == []
    assert unlock is not None
    assert unlock.pct_supply == 2.5
    assert unlock.at == datetime(2026, 8, 10, tzinfo=UTC)


async def test_a_cached_empty_schedule_is_served_as_a_known_absence() -> None:
    cache = FakeCache({"defillama:unlock:aave": {"at": None, "pct_supply": None}})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch")

    result = await _client(handler, cache).unlock("aave", "aave")
    assert result is None


async def test_fetching_an_unlock_caches_the_extraction_not_the_body() -> None:
    document = {
        "gecko_id": "aave",
        "metadata": {
            "events": [
                {
                    "timestamp": int(datetime(2099, 1, 1, tzinfo=UTC).timestamp()),
                    "noOfTokens": [25_000],
                }
            ]
        },
        "supplyMetrics": {"maxSupply": 1_000_000.0},
        "documentedData": {"huge": "x" * 10_000},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    cache = FakeCache()
    # 2099 is outside the 30-day horizon, so the extraction is an empty schedule.
    await _client(handler, cache).unlock("aave", "aave")
    key, value, ttl = cache.writes[0]
    assert key == "defillama:unlock:aave"
    assert value == {"at": None, "pct_supply": None}
    assert ttl == client_mod.UNLOCK_TTL_SECONDS
    assert "documentedData" not in str(value)


async def test_a_failed_unlock_fetch_returns_absent_without_raising() -> None:
    # One protocol's 2.25 MB document failing must not abort the cycle for the
    # others, and must never be substituted by a zero.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cache = FakeCache()
    result = await _client(handler, cache).unlock("aave", "aave")
    assert result is None
    assert cache.writes == []  # nothing poisoned the cache


async def test_protocols_are_returned_as_a_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/protocols"
        return httpx.Response(
            200, json=[{"slug": "aave-v3", "gecko_id": "aave", "tvl": 1.0}]
        )

    rows = await _client(handler, FakeCache()).protocols()
    assert rows[0]["slug"] == "aave-v3"


async def test_fees_are_keyed_by_slug_and_exclude_the_chart_series() -> None:
    # The exclude params take the response from 24.6 MB to 3.7 MB. Dropping
    # them would move 3.5 GB a day to read a handful of numbers, so they are
    # asserted rather than trusted.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["excludeTotalDataChart"] == "true"
        assert request.url.params["excludeTotalDataChartBreakdown"] == "true"
        return httpx.Response(
            200,
            json={
                "protocols": [
                    {"slug": "aave-v3", "total24h": 42.0, "total7d": 200.0},
                    {"name": "no slug", "total24h": 1.0},
                ]
            },
        )

    fees = await _client(handler, FakeCache()).fees()
    assert fees["aave-v3"]["total24h"] == 42.0
    # A row without a slug cannot be joined to anything, so it is dropped
    # rather than keyed under an empty string.
    assert len(fees) == 1


async def test_emission_slugs_are_returned_as_a_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["aave", "drift", "benddao"])

    slugs = await _client(handler, FakeCache()).emission_slugs()
    assert slugs == {"aave", "drift", "benddao"}
