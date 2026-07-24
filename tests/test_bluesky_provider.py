"""BlueskyProvider: public searchPosts -> one RawItem per crypto post."""

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
    / "services"
    / "collector-social"
    / "app"
    / "providers"
    / "bluesky.py",
)
bsky = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = bsky
_spec.loader.exec_module(bsky)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _post(text: str, did: str, likes: int = 0) -> dict:
    return {
        "uri": f"at://{did}/{likes}",
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
    provider = bsky.BlueskyProvider()

    items = await provider.fetch()
    await provider.close()

    assert {i.source for i in items} == {"bluesky"}
    assert all(i.kind == "social" for i in items)
    btc = [i for i in items if "BTC" in i.symbols]
    assert len(btc) == 2  # two posts mention $BTC
    assert btc[0].external_id  # a stable post id
    assert "$BTC" in btc[0].text


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(bsky.SEARCH_URL).mock(return_value=httpx.Response(429))
    provider = bsky.BlueskyProvider()

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
