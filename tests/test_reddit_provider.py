"""RedditProvider: /new -> SocialEvent; quota exhaustion raises RateLimitedError."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "reddit_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "reddit.py",
)
rd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)

from cmi_common.sources import RateLimitedError  # noqa: E402


class FakeCache:
    def __init__(self, allow: bool = True) -> None:
        self.stored: dict[str, object] = {}
        self._allow = allow

    async def allow(self, *_a) -> bool:
        return self._allow

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


def _post(title: str, author: str, score: int = 0, comments: int = 0) -> dict:
    return {
        "data": {
            "title": title,
            "selftext": "",
            "author": author,
            "score": score,
            "num_comments": comments,
        }
    }


@respx.mock
async def test_aggregates_cashtags_from_new() -> None:
    respx.get("https://www.reddit.com/r/CryptoCurrency/new.json").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"children": [
                _post("$BTC to the moon", "u1", score=10, comments=5),
                _post("holding $BTC", "u2", score=3, comments=1),
            ]}},
        )
    )
    provider = rd.RedditProvider(FakeCache(), subreddits=["CryptoCurrency"])

    events = await provider.fetch()
    await provider.close()

    assert len(events) == 1
    btc = events[0]
    assert btc.symbol == "BTC"
    assert btc.platform == "reddit"
    assert btc.source == "reddit"
    assert btc.mentions == 2
    assert btc.engagement_score == 19.0  # 10+5 + 3+1


async def test_quota_exhausted_raises_rate_limited() -> None:
    provider = rd.RedditProvider(FakeCache(allow=False), subreddits=["CryptoCurrency"])

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
