"""Twitter/X collector: tweets -> aggregated SocialEvent per symbol."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import respx

# Load the collector module directly (services aren't an importable package).
_spec = importlib.util.spec_from_file_location(
    "tw_collector",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-twitter" / "app" / "collector.py",
)
tw = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = tw
_spec.loader.exec_module(tw)


class FakeCache:
    def __init__(self) -> None:
        self.stored: dict[str, object] = {}

    async def allow(self, *_a) -> bool:
        return True

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


class FakeProducer:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, _topic, event) -> None:
        self.published.append(event)


def _tweet(text: str, author: str, likes: int = 0) -> dict:
    return {
        "text": text,
        "author_id": author,
        "public_metrics": {
            "like_count": likes,
            "retweet_count": 0,
            "reply_count": 0,
            "quote_count": 0,
        },
    }


@respx.mock
async def test_aggregates_cashtags_into_social_events() -> None:
    respx.get(tw.SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _tweet("$BTC breakout, bullish!", "a1", likes=10),
                    _tweet("still holding $BTC to the moon", "a2", likes=5),
                    _tweet("$ETH looking strong", "a3", likes=2),
                ]
            },
        )
    )
    producer = FakeProducer()
    collector = tw.TwitterCollector(FakeCache(), producer, bearer_token="tok")

    published = await collector.poll_once()
    await collector.close()

    assert published == 2
    by_symbol = {e.symbol: e for e in producer.published}
    assert set(by_symbol) == {"BTC", "ETH"}
    btc = by_symbol["BTC"]
    assert btc.platform == "twitter"
    assert btc.source == "twitter"  # use_enum_values -> plain string
    assert btc.mentions == 2
    assert btc.unique_authors == 2
    assert btc.engagement_score == 15.0
    assert "$BTC" in btc.text_sample


@respx.mock
async def test_no_bearer_token_is_noop() -> None:
    route = respx.get(tw.SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    producer = FakeProducer()
    collector = tw.TwitterCollector(FakeCache(), producer, bearer_token=None)

    published = await collector.poll_once()
    await collector.close()

    assert published == 0
    assert producer.published == []
    assert route.called is False  # never hits the API without a token


@respx.mock
async def test_http_error_degrades_without_raising() -> None:
    respx.get(tw.SEARCH_URL).mock(return_value=httpx.Response(429))
    producer = FakeProducer()
    collector = tw.TwitterCollector(FakeCache(), producer, bearer_token="tok")

    published = await collector.poll_once()
    await collector.close()

    assert published == 0
    assert producer.published == []
