"""CryptoCompareNewsProvider: /data/v2/news -> RawItem; 429 -> RateLimitedError."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "cc_news_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-news"
    / "app"
    / "providers"
    / "cryptocompare.py",
)
cc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _article(aid: int, title: str) -> dict:
    return {
        "id": aid,
        "title": title,
        "body": "body text",
        "url": "https://example.com/a",
        "published_on": 1700000000,
        "source_info": {"name": "CoinDesk"},
        "categories": "BTC|Trading",
    }


@respx.mock
async def test_yields_raw_item_per_article() -> None:
    respx.get("https://min-api.cryptocompare.com/data/v2/news/").mock(
        return_value=httpx.Response(
            200,
            json={"Data": [_article(2, "second"), _article(1, "first")]},
        )
    )
    provider = cc.CryptoCompareNewsProvider("https://min-api.cryptocompare.com", None)

    items = await provider.fetch()
    await provider.close()

    assert [i.title for i in items] == ["second", "first"]
    assert {i.source for i in items} == {"cryptocompare"}
    assert all(i.kind == "news" for i in items)
    assert items[0].external_id == "2"
    assert items[0].symbols == ["BTC"]
    assert items[0].text == "body text"


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get("https://min-api.cryptocompare.com/data/v2/news/").mock(
        return_value=httpx.Response(429)
    )
    provider = cc.CryptoCompareNewsProvider("https://min-api.cryptocompare.com", None)

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
