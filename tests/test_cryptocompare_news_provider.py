"""CryptoCompareNewsProvider: /data/v2/news -> NewsEvent; quota -> RateLimited."""

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
    / "services" / "collector-news" / "app" / "providers" / "cryptocompare.py",
)
cc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)

from cmi_common.sources import RateLimited  # noqa: E402


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
async def test_publishes_new_articles() -> None:
    respx.get("https://min-api.cryptocompare.com/data/v2/news/").mock(
        return_value=httpx.Response(200, json={"Data": [
            _article(2, "second"), _article(1, "first"),
        ]})
    )
    provider = cc.CryptoCompareNewsProvider(
        "https://min-api.cryptocompare.com", None, FakeCache()
    )

    events = await provider.fetch()
    await provider.close()

    assert [e.title for e in events] == ["second", "first"]
    assert events[0].symbols == ["BTC"]
    assert events[0].source == "cryptocompare"


async def test_quota_exhausted_raises_rate_limited() -> None:
    provider = cc.CryptoCompareNewsProvider(
        "https://min-api.cryptocompare.com", None, FakeCache(allow=False)
    )

    with pytest.raises(RateLimited):
        await provider.fetch()
    await provider.close()
