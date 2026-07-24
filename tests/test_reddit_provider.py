"""RedditProvider: /new -> one RawItem per crypto post; 429 -> RateLimitedError."""

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
    / "services"
    / "collector-social"
    / "app"
    / "providers"
    / "reddit.py",
)
rd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _post(
    name: str, title: str, author: str, score: int = 0, comments: int = 0
) -> dict:
    return {
        "data": {
            "name": name,
            "title": title,
            "selftext": "",
            "author": author,
            "score": score,
            "num_comments": comments,
        }
    }


@respx.mock
async def test_yields_raw_item_per_crypto_post() -> None:
    respx.get("https://www.reddit.com/r/CryptoCurrency/new.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "children": [
                        _post("t3_1", "$BTC to the moon", "u1", score=10, comments=5),
                        _post("t3_2", "holding $BTC", "u2", score=3, comments=1),
                        _post("t3_3", "no cashtag here", "u3", score=9, comments=2),
                    ]
                }
            },
        )
    )
    provider = rd.RedditProvider(subreddits=["CryptoCurrency"])

    items = await provider.fetch()
    await provider.close()

    assert {i.source for i in items} == {"reddit"}
    assert all(i.kind == "social" for i in items)
    btc = [i for i in items if "BTC" in i.symbols]
    assert len(btc) == 2  # symbolless post skipped
    first = next(i for i in btc if i.external_id == "t3_1")
    assert "$BTC to the moon" in first.text
    assert first.engagement == 15.0  # 10 + 5


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get("https://www.reddit.com/r/CryptoCurrency/new.json").mock(
        return_value=httpx.Response(429)
    )
    provider = rd.RedditProvider(subreddits=["CryptoCurrency"])

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
