"""BlueskyProvider: public searchPosts -> aggregated SocialEvent per cashtag."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "bsky_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "bluesky.py",
)
bsky = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = bsky
_spec.loader.exec_module(bsky)

from cmi_common.sources import RateLimited  # noqa: E402


class FakeCache:
    def __init__(self) -> None:
        self.stored: dict[str, object] = {}

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


def _post(text: str, did: str, likes: int = 0) -> dict:
    return {
        "record": {"text": text},
        "author": {"did": did},
        "likeCount": likes,
        "repostCount": 0,
        "replyCount": 0,
    }


@respx.mock
async def test_aggregates_cashtags() -> None:
    respx.get(bsky.SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "posts": [
                    _post("$BTC breakout bullish", "did:a", likes=10),
                    _post("still holding $BTC", "did:b", likes=5),
                    _post("$ETH strong", "did:c", likes=2),
                ]
            },
        )
    )
    provider = bsky.BlueskyProvider(FakeCache())

    events = await provider.fetch()
    await provider.close()

    by_symbol = {e.symbol: e for e in events}
    assert set(by_symbol) == {"BTC", "ETH"}
    btc = by_symbol["BTC"]
    assert btc.platform == "bluesky"
    assert btc.source == "bluesky"  # use_enum_values -> plain string
    assert btc.mentions == 2
    assert btc.unique_authors == 2
    assert btc.engagement_score == 15.0
    assert "$BTC" in btc.text_sample


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(bsky.SEARCH_URL).mock(return_value=httpx.Response(429))
    provider = bsky.BlueskyProvider(FakeCache())

    with pytest.raises(RateLimited):
        await provider.fetch()
    await provider.close()
