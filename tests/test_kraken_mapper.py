"""Pure tests for Kraken OHLC / Depth payload mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from service_modules import load_service_module

from cmi_common.sources import RateLimitedError

mapper = load_service_module("collector-kraken", "domain.mapper")
parse_depth = mapper.parse_depth
parse_ohlc = mapper.parse_ohlc
candle_event = mapper.candle_event
OhlcCandle = mapper.OhlcCandle


OHLC_ROW = [
    1690000000,
    "29000.0",
    "29100.0",
    "28900.0",
    "29050.0",
    "29010.0",
    "123.45",
    42,
]


def test_parse_ohlc_maps_every_field_as_decimal():
    payload = {"error": [], "result": {"XXBTZUSD": [OHLC_ROW], "last": 1690003600}}
    candles = parse_ohlc(payload, symbol="BTC", interval="1h")
    assert len(candles) == 1
    c = candles[0]
    assert c.time == datetime(2023, 7, 22, 4, 26, 40, tzinfo=UTC)
    assert c.symbol == "BTC"
    assert c.interval == "1h"
    assert c.open == Decimal("29000.0")
    assert c.high == Decimal("29100.0")
    assert c.low == Decimal("28900.0")
    assert c.close == Decimal("29050.0")
    assert c.vwap == Decimal("29010.0")
    assert c.volume == Decimal("123.45")
    assert c.trades == 42


def test_parse_ohlc_ignores_the_last_cursor_key():
    """`last` sits beside the pair key in `result` and is an int, not a series."""
    payload = {"error": [], "result": {"XXBTZUSD": [OHLC_ROW], "last": 1690003600}}
    assert len(parse_ohlc(payload, symbol="BTC", interval="1h")) == 1


def test_parse_ohlc_returns_empty_on_missing_result():
    assert parse_ohlc({"error": [], "result": {}}, symbol="BTC", interval="1h") == []


def test_parse_ohlc_skips_malformed_rows():
    payload = {"error": [], "result": {"X": [OHLC_ROW, [1, "2"]]}}
    assert len(parse_ohlc(payload, symbol="BTC", interval="1h")) == 1


def _ohlc_candle(**overrides) -> OhlcCandle:
    fields = dict(
        time=datetime(2023, 7, 22, 4, 26, 40, tzinfo=UTC),
        symbol="BTC",
        interval="1h",
        open=Decimal("29000.0"),
        high=Decimal("29100.0"),
        low=Decimal("28900.0"),
        close=Decimal("29050.0"),
        vwap=Decimal("29010.0"),
        volume=Decimal("123.45"),
        trades=42,
    )
    fields.update(overrides)
    return OhlcCandle(**fields)


def test_candle_event_maps_every_field():
    c = _ohlc_candle()
    e = candle_event(c)
    assert e.source == "kraken"
    assert e.occurred_at == c.time
    assert e.symbol == "BTC"
    assert e.interval == "1h"
    assert e.open == Decimal("29000.0")
    assert e.high == Decimal("29100.0")
    assert e.low == Decimal("28900.0")
    assert e.close == Decimal("29050.0")
    assert e.vwap == Decimal("29010.0")
    assert e.volume == Decimal("123.45")
    assert e.trades == 42
    assert e.venue == "kraken"


def test_candle_event_maps_zero_vwap_to_none():
    """Kraken returns vwap=0 for a window with no trades (open/high/low/close
    still carry the previous close) -- a price cannot legitimately be zero, so
    that reading is unmeasured, not a measured zero."""
    c = _ohlc_candle(vwap=Decimal("0"), volume=Decimal("0"))
    e = candle_event(c)
    assert e.vwap is None


DEPTH = {
    "error": [],
    "result": {
        "XXBTZUSD": {
            "asks": [["100.0", "2.0", 1], ["101.0", "3.0", 1], ["200.0", "9.0", 1]],
            "bids": [["99.0", "4.0", 1], ["98.5", "1.0", 1], ["10.0", "9.0", 1]],
        }
    },
}


def test_parse_depth_computes_mid_and_spread():
    snap = parse_depth(DEPTH, symbol="BTC")
    assert snap.symbol == "BTC"
    assert snap.mid_price == Decimal("99.5")
    # (100 - 99) / 99.5 * 100
    assert round(snap.spread_pct, 4) == round(
        float(Decimal("1") / Decimal("99.5") * 100), 4
    )


def test_parse_depth_sums_only_the_band_around_mid():
    """+/-1% of a 99.5 mid is [98.505, 100.495]: the 200 ask and 98.5/10 bids
    fall out."""
    snap = parse_depth(DEPTH, symbol="BTC", band_pct=1.0)
    assert snap.ask_depth_usd == Decimal("100.0") * Decimal("2.0")
    assert snap.bid_depth_usd == Decimal("99.0") * Decimal("4.0")


def test_parse_depth_returns_none_on_one_sided_book():
    payload = {"error": [], "result": {"X": {"asks": [], "bids": [["1", "1", 1]]}}}
    assert parse_depth(payload, symbol="BTC") is None


def test_parse_depth_returns_none_on_missing_result():
    assert parse_depth({"error": [], "result": {}}, symbol="BTC") is None


kraken = load_service_module("collector-kraken", "infrastructure.kraken")
KrakenPublicClient = kraken.KrakenPublicClient


def _client(handler):
    c = KrakenPublicClient()
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_ohlc_returns_the_payload_on_success():
    def handler(request):
        assert request.url.params["interval"] == "60"
        return httpx.Response(200, json={"error": [], "result": {"X": [OHLC_ROW]}})

    client = _client(handler)
    payload = await client.ohlc("XXBTZUSD", interval_minutes=60)
    assert payload["result"]["X"] == [OHLC_ROW]
    await client.close()


@pytest.mark.asyncio
async def test_ohlc_raises_rate_limited_on_error_array_despite_http_200():
    """Kraken signals throttling in the body, not the status code."""

    def handler(request):
        return httpx.Response(
            200, json={"error": ["EAPI:Rate limit exceeded"], "result": {}}
        )

    client = _client(handler)
    with pytest.raises(RateLimitedError):
        await client.ohlc("XXBTZUSD", interval_minutes=60)
    await client.close()


@pytest.mark.asyncio
async def test_ohlc_raises_on_a_non_ratelimit_error_array():
    def handler(request):
        return httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"]})

    client = _client(handler)
    with pytest.raises(ValueError, match="Unknown asset pair"):
        await client.ohlc("NOPE", interval_minutes=60)
    await client.close()


@pytest.mark.asyncio
async def test_ohlc_raises_rate_limited_on_http_429():
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "17"}, json={})

    client = _client(handler)
    with pytest.raises(RateLimitedError) as exc:
        await client.ohlc("XXBTZUSD", interval_minutes=60)
    assert exc.value.retry_after == 17
    await client.close()
