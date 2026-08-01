"""The client's caching and failure behaviour, without touching the network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from service_modules import load_service_module

from cmi_common.observability import UPSTREAM_REQUESTS

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
        {
            "defillama:unlock:aave": {
                "at": "2026-08-10T00:00:00+00:00",
                "pct_supply": 2.5,
            }
        }
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
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    assert await _client(handler, cache).unlock("aave", "aave") is None
    assert calls == []


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
        # The unlock endpoint lives on the dataset CDN, never on api.llama.fi
        # — that host's /emissions is the 402-Payment-Required Pro endpoint,
        # the single most expensive mistake this module can make.
        assert request.url.host == "defillama-datasets.llama.fi"
        assert request.url.path == "/emissions/aave"
        return httpx.Response(200, json=document)

    cache = FakeCache()
    # 2099 is outside the 30-day horizon, so the extraction is an empty schedule.
    await _client(handler, cache).unlock("aave", "aave")
    key, value, ttl = cache.writes[0]
    assert key == "defillama:unlock:aave"
    assert value == {"at": None, "pct_supply": None}
    assert ttl == client_mod.UNLOCK_TTL_SECONDS
    assert "documentedData" not in str(value)


async def test_a_round_tripped_unlock_survives_a_tiny_pct_supply() -> None:
    # _from_cache's real parsing is otherwise only ever exercised against a
    # hand-written literal. Round-trip through one client's write and a
    # second client's read, with a pct_supply that rounds to 0.0 — a genuine
    # small unlock does that, and it must survive as a measured number, not
    # collapse into the "nothing coming" shape.
    at = datetime.now(tz=UTC) + timedelta(days=3)
    document = {
        "metadata": {"events": [{"timestamp": int(at.timestamp()), "noOfTokens": [1]}]},
        "supplyMetrics": {"maxSupply": 1_000_000_000.0},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    cache = FakeCache()
    written = await _client(handler, cache).unlock("aave", "aave")
    assert written is not None
    assert written.pct_supply == 0.0

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch")

    read_back = await _client(unreachable, cache).unlock("aave", "aave")
    assert read_back is not None
    assert read_back.pct_supply == 0.0
    assert read_back.at == written.at


async def test_an_unsizable_schedule_propagates_rather_than_returning_none() -> None:
    # next_unlock raises when an unlock is scheduled but cannot be sized. That
    # exception must escape unlock(), because the caller's except is what
    # leaves the coin id absent. Swallowed here, it becomes "nothing is
    # coming" instead of "unknown".
    document = {
        "metadata": {
            "events": [
                {
                    "timestamp": int(
                        (datetime.now(tz=UTC) + timedelta(days=3)).timestamp()
                    ),
                    "noOfTokens": [25_000],
                }
            ]
        },
        "supplyMetrics": {"maxSupply": 0.0},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    cache = FakeCache()
    with pytest.raises(ValueError):
        await _client(handler, cache).unlock("aave", "aave")
    assert cache.writes == []


async def test_a_failed_fetch_propagates_rather_than_returning_none() -> None:
    # Same reasoning: a fetch we could not complete is "unknown", and only a
    # raise conveys that to the caller. One protocol's document failing must
    # not silently become a zero, and the caller (not this method) is what
    # keeps that failure from aborting the rest of the cycle.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cache = FakeCache()
    with pytest.raises(httpx.HTTPError):
        await _client(handler, cache).unlock("aave", "aave")
    assert cache.writes == []


async def test_budget_exhaustion_propagates_too() -> None:
    class DeniedCache(FakeCache):
        async def allow(self, key: str, limit: int, window: int) -> bool:
            return False

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the network")

    with pytest.raises(RuntimeError):
        await _client(handler, DeniedCache()).unlock("aave", "aave")


async def test_budget_exhaustion_is_labelled_throttled_not_error() -> None:
    # "We did not look" and "we looked and it broke" must stay distinguishable
    # in the metrics, or a throttled cycle reads as a healthy one.
    class DeniedCache(FakeCache):
        async def allow(self, key: str, limit: int, window: int) -> bool:
            return False

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the network")

    before = UPSTREAM_REQUESTS.labels(
        client_mod.SERVICE, "defillama", "throttled"
    )._value.get()
    with pytest.raises(RuntimeError):
        await _client(handler, DeniedCache()).unlock("aave", "aave")
    after = UPSTREAM_REQUESTS.labels(
        client_mod.SERVICE, "defillama", "throttled"
    )._value.get()
    assert after == before + 1


async def test_an_unrecognised_cache_entry_is_treated_as_a_miss() -> None:
    # Cache entries live 24h under an unversioned key. A payload-shape change
    # must not make the new reader serve a confident absence for a row it
    # never actually parsed — it should fall through and fetch instead.
    cache = FakeCache({"defillama:unlock:aave": {"some": "old-shape"}})
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    result = await _client(handler, cache).unlock("aave", "aave")
    assert calls != []
    assert result is None  # the fetched empty-ish document also has no schedule


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
        # Same host as the unlock documents, never api.llama.fi's paid tier.
        assert request.url.host == "defillama-datasets.llama.fi"
        assert request.url.path == "/emissionsProtocolsList"
        return httpx.Response(200, json=["aave", "drift", "benddao"])

    slugs = await _client(handler, FakeCache()).emission_slugs()
    assert slugs == {"aave", "drift", "benddao"}
