"""collector-coingecko publishes the token universe for the content collectors."""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

from cmi_common.sources import LEXICON_KEY

CoinGeckoCollector = load_service_module(
    "collector-coingecko", "application.collector"
).CoinGeckoCollector


class FakeClient:
    async def trending(self) -> list[str]:
        return []

    async def markets(self, per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        if page > 1:
            return []
        return [
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 60000.0,
                "total_volume": 1.0,
                "market_cap": 1.0,
            },
        ]


class FakeProducer:
    async def publish(self, topic: Any, event: Any) -> None:
        return None


class FakeCache:
    def __init__(self) -> None:
        self.written: dict[str, Any] = {}

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.written[key] = value


async def test_poll_writes_ticker_and_name_pairs_to_the_lexicon_key() -> None:
    cache = FakeCache()
    collector = CoinGeckoCollector(FakeClient(), FakeProducer(), cache=cache, pages=1)
    await collector.poll_once()
    assert cache.written[LEXICON_KEY] == [{"ticker": "BTC", "name": "Bitcoin"}]


async def test_lexicon_write_failure_does_not_break_the_poll() -> None:
    class BrokenCache(FakeCache):
        async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
            raise RuntimeError("redis down")

    collector = CoinGeckoCollector(
        FakeClient(), FakeProducer(), cache=BrokenCache(), pages=1
    )
    published = await collector.poll_once()
    assert published >= 1  # price events still went out
