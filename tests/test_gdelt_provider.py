"""GdeltProvider: GDELT DOC 2.0 artlist JSON -> RawItem per article."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "gdelt_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-news"
    / "app"
    / "providers"
    / "gdelt.py",
)
gd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = gd
_spec.loader.exec_module(gd)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _article(url: str, title: str) -> dict:
    return {
        "url": url,
        "title": title,
        "seendate": "20240102T120000Z",
        "domain": "example.com",
        "language": "English",
        "sourcecountry": "US",
    }


@respx.mock
async def test_maps_articles_to_rawitems() -> None:
    respx.get(gd.DOC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [
                    _article("https://x/a", "Bitcoin surges"),
                    _article("https://x/b", "Ether update"),
                ],
            },
        )
    )
    provider = gd.GdeltProvider()
    items = await provider.fetch()
    await provider.close()

    assert {i.title for i in items} == {"Bitcoin surges", "Ether update"}
    a = next(i for i in items if i.title == "Bitcoin surges")
    assert a.source == "gdelt"
    assert a.kind == "news"
    assert a.external_id == "https://x/a"
    assert str(a.url) == "https://x/a"
    assert a.published_at is not None  # seendate parsed


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(gd.DOC_URL).mock(return_value=httpx.Response(429))
    provider = gd.GdeltProvider()
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


@respx.mock
async def test_empty_or_bad_json_returns_empty() -> None:
    respx.get(gd.DOC_URL).mock(return_value=httpx.Response(200, text="not json"))
    provider = gd.GdeltProvider()
    assert await provider.fetch() == []
    await provider.close()
