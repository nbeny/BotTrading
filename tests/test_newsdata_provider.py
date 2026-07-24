"""NewsDataProvider: /api/1/crypto results[] -> RawItem per article."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "newsdata_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-news"
    / "app"
    / "providers"
    / "newsdata.py",
)
nd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = nd
_spec.loader.exec_module(nd)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _article(aid: str, title: str) -> dict:
    return {
        "article_id": aid,
        "title": title,
        "link": "https://x/a",
        "description": "body text",
        "pubDate": "2024-01-02 12:00:00",
        "coin": ["btc"],
        "language": "english",
    }


@respx.mock
async def test_maps_results_to_rawitems() -> None:
    respx.get("https://newsdata.io/api/1/crypto").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "results": [
                    _article("a1", "Bitcoin surges"),
                    _article("a2", "Ether news"),
                ],
            },
        )
    )
    provider = nd.NewsDataProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert {i.title for i in items} == {"Bitcoin surges", "Ether news"}
    a = next(i for i in items if i.external_id == "a1")
    assert a.source == "newsdata"
    assert a.kind == "news"
    assert a.symbols == ["BTC"]
    assert str(a.url) == "https://x/a"
    assert a.published_at is not None


async def test_no_key_is_noop() -> None:
    provider = nd.NewsDataProvider(None)
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get("https://newsdata.io/api/1/crypto").mock(return_value=httpx.Response(429))
    provider = nd.NewsDataProvider("KEY")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


@respx.mock
async def test_non_dict_body_degrades_to_empty() -> None:
    respx.get("https://newsdata.io/api/1/crypto").mock(
        return_value=httpx.Response(200, json=[])
    )
    provider = nd.NewsDataProvider("KEY")
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_null_article_in_results_is_skipped() -> None:
    respx.get("https://newsdata.io/api/1/crypto").mock(
        return_value=httpx.Response(
            200, json={"results": [None, _article("a9", "Solana surges")]}
        )
    )
    provider = nd.NewsDataProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert [i.external_id for i in items] == ["a9"]
