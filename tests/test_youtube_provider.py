"""YouTubeProvider: search.list items -> RawItem per crypto video."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "youtube_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-social"
    / "app"
    / "providers"
    / "youtube.py",
)
yt = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = yt
_spec.loader.exec_module(yt)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _item(vid: str, title: str) -> dict:
    return {
        "id": {"videoId": vid},
        "snippet": {
            "title": title,
            "description": "desc",
            "channelTitle": "CryptoChan",
            "publishedAt": "2024-01-02T12:00:00Z",
        },
    }


@respx.mock
async def test_maps_videos_to_rawitems() -> None:
    respx.get(yt.SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [_item("v1", "Bitcoin analysis"), _item("v2", "ETH update")]
            },
        )
    )
    provider = yt.YouTubeProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert {i.external_id for i in items} == {"v1", "v2"}
    a = next(i for i in items if i.external_id == "v1")
    assert a.source == "youtube"
    assert a.kind == "social"
    assert "Bitcoin analysis" in a.text
    assert str(a.url) == "https://www.youtube.com/watch?v=v1"
    assert a.author == "CryptoChan"
    assert a.published_at is not None


async def test_no_key_is_noop() -> None:
    provider = yt.YouTubeProvider(None)
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_quota_exceeded_403_raises_rate_limited() -> None:
    respx.get(yt.SEARCH_URL).mock(
        return_value=httpx.Response(
            403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}
        )
    )
    provider = yt.YouTubeProvider("KEY")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
