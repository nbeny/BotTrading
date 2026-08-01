"""api-gateway sentiment endpoints delegate to SentimentAggReader."""

from __future__ import annotations

from service_modules import load_service_module

read_api = load_service_module("api-gateway", "read_api")


class _StubReader:
    def __init__(self) -> None:
        self.calls = []

    async def all_windows(self, *, symbol, kind, half_life_h=None):
        self.calls.append(("all_windows", symbol, kind, half_life_h))
        return [
            {
                "window": "1h",
                "avg": 0.5,
                "weighted_avg": 0.4,
                "mentions": 3,
                "engagement": 2.0,
            }
        ]

    async def series(self, *, symbol, kind, points):
        self.calls.append(("series", symbol, kind, points))
        return [{"hour": "2024-01-01T10:00:00+00:00", "sentiment": 0.5, "mentions": 3}]

    async def distinct_authors(self, *, symbol, window):
        self.calls.append(("authors", symbol, window))
        return 7


# _StubReader stands in for SqlSentimentAggReader; endpoints take `reader` as
# their last param so tests bypass the get_reader_dep/Query machinery.
async def test_windows_endpoint_passes_decay() -> None:
    reader = _StubReader()
    out = await read_api.sentiment_windows(
        symbol="btc", kind="all", decay=6.0, reader=reader
    )
    assert out[0]["window"] == "1h"
    assert reader.calls[0] == ("all_windows", "BTC", "all", 6.0)


async def test_series_endpoint_defaults() -> None:
    reader = _StubReader()
    out = await read_api.sentiment_series(symbol="eth", points=12, reader=reader)
    assert out[0]["mentions"] == 3
    assert reader.calls[0] == ("series", "ETH", None, 12)


async def test_authors_endpoint() -> None:
    reader = _StubReader()
    out = await read_api.sentiment_authors(symbol="btc", window="7d", reader=reader)
    assert out == {"symbol": "BTC", "window": "7d", "unique_authors": 7}
