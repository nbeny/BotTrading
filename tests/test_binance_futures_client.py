"""Binance futures HTTP behaviour, without touching the network."""

from __future__ import annotations

import httpx
import pytest
from service_modules import load_service_module

client_mod = load_service_module(
    "collector-binance-futures", "infrastructure.binance_client"
)


class FakeCache:
    def __init__(self, allow: bool = True) -> None:
        self._allow = allow

    async def allow(self, key: str, limit: int, window: int) -> bool:
        return self._allow


def _client(handler, cache: FakeCache | None = None):
    return client_mod.BinanceFuturesClient(
        cache or FakeCache(), transport=httpx.MockTransport(handler)
    )


async def test_premium_index_returns_every_perp_in_one_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "fapi.binance.com"
        assert request.url.path == "/fapi/v1/premiumIndex"
        return httpx.Response(
            200,
            json=[
                {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
                {"symbol": "ETHUSDT", "lastFundingRate": "-0.0002"},
            ],
        )

    rows = await _client(handler).premium_index()
    assert [r["symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT"]


async def test_open_interest_history_yields_the_usd_level_and_the_24h_change() -> None:
    # One request gives both. The level comes from sumOpenInterestValue (USD),
    # the change from sumOpenInterest (base units) -- the USD series moves with
    # price as well as with positioning, so it cannot answer "is conviction
    # entering the book".
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/futures/data/openInterestHist"
        assert request.url.params["period"] == "1h"
        assert request.url.params["limit"] == "25"
        return httpx.Response(
            200,
            json=[
                {
                    "sumOpenInterest": "100.0",
                    "sumOpenInterestValue": "1000000.0",
                    "timestamp": 1785445200000,
                },
                {
                    "sumOpenInterest": "110.0",
                    "sumOpenInterestValue": "1100000.0",
                    "timestamp": 1785531600000,
                },
            ],
        )

    reading = await _client(handler).open_interest("BTCUSDT")
    assert reading is not None
    assert reading.usd == 1100000.0
    assert reading.change_pct_24h == 10.0


async def test_a_single_open_interest_point_yields_a_level_but_no_change() -> None:
    # A level without a baseline is a level, not a trend. Reporting 0.0 would
    # claim flat positioning we never measured.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "sumOpenInterest": "100.0",
                    "sumOpenInterestValue": "1000000.0",
                    "timestamp": 1785445200000,
                }
            ],
        )

    reading = await _client(handler).open_interest("BTCUSDT")
    assert reading is not None
    assert reading.usd == 1000000.0
    assert reading.change_pct_24h is None


async def test_a_zero_oi_baseline_yields_no_change_rather_than_a_division() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"sumOpenInterest": "0", "sumOpenInterestValue": "0", "timestamp": 1},
                {
                    "sumOpenInterest": "50",
                    "sumOpenInterestValue": "500",
                    "timestamp": 2,
                },
            ],
        )

    reading = await _client(handler).open_interest("BTCUSDT")
    assert reading is not None
    assert reading.usd == 500.0
    assert reading.change_pct_24h is None


async def test_an_empty_open_interest_history_yields_no_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await _client(handler).open_interest("BTCUSDT") is None


async def test_long_short_ratio_reads_the_most_recent_bucket() -> None:
    # Binance returns oldest first, so the current reading is the last element.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/futures/data/globalLongShortAccountRatio"
        return httpx.Response(
            200,
            json=[
                {"longShortRatio": "1.5", "timestamp": 1},
                {"longShortRatio": "1.9", "timestamp": 2},
            ],
        )

    assert await _client(handler).long_short_ratio("BTCUSDT") == 1.9


async def test_an_empty_ratio_response_yields_none_not_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await _client(handler).long_short_ratio("BTCUSDT") is None


async def test_used_weight_header_is_recorded_for_self_throttling() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "1800"})

    client = _client(handler)
    await client.premium_index()
    assert client.used_weight == 1800
    assert client.near_weight_ceiling is True


async def test_weight_below_the_ceiling_does_not_trip_the_flag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "10"})

    client = _client(handler)
    await client.premium_index()
    assert client.used_weight == 10
    assert client.near_weight_ceiling is False


async def test_rate_limit_status_raises_a_typed_error_with_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"})

    with pytest.raises(client_mod.BinanceRateLimitedError) as excinfo:
        await _client(handler).premium_index()
    assert excinfo.value.retry_after == 12.0


async def test_a_ban_status_raises_the_same_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(418)

    with pytest.raises(client_mod.BinanceRateLimitedError) as excinfo:
        await _client(handler).premium_index()
    assert excinfo.value.retry_after is None


async def test_a_server_error_propagates() -> None:
    # Not swallowed into a None: an unread value must never reach the scorer as
    # a measured one.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPError):
        await _client(handler).premium_index()


async def test_budget_exhaustion_raises_rather_than_returning_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the network")

    with pytest.raises(RuntimeError):
        await _client(handler, FakeCache(allow=False)).premium_index()
