"""MastodonProvider: hashtag timeline statuses -> RawItem per crypto post."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "masto_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "mastodon.py",
)
ms = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = ms
_spec.loader.exec_module(ms)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _status(sid: str, html: str, acct: str, faves: int = 0) -> dict:
    return {
        "id": sid, "content": html, "url": f"https://m/{sid}",
        "created_at": "2024-01-02T12:00:00.000Z", "language": "en",
        "account": {"acct": acct},
        "favourites_count": faves, "reblogs_count": 0, "replies_count": 0,
    }


@respx.mock
async def test_maps_statuses_with_cashtags() -> None:
    url = "https://mastodon.social/api/v1/timelines/tag/crypto"
    respx.get(url).mock(return_value=httpx.Response(200, json=[
        _status("1", "<p>$BTC breaking out</p>", "alice", faves=4),
        _status("2", "<p>no ticker here</p>", "bob"),
    ]))
    provider = ms.MastodonProvider(instance="mastodon.social", hashtag="crypto")
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1               # only the $BTC status
    it = items[0]
    assert it.source == "mastodon"
    assert it.kind == "social"
    assert it.external_id == "1"
    assert it.symbols == ["BTC"]
    assert "$BTC" in it.text             # HTML stripped
    assert it.engagement == 4.0
    assert it.author == "alice"


@respx.mock
async def test_429_raises_rate_limited() -> None:
    url = "https://mastodon.social/api/v1/timelines/tag/crypto"
    respx.get(url).mock(return_value=httpx.Response(429))
    provider = ms.MastodonProvider(instance="mastodon.social", hashtag="crypto")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


@respx.mock
async def test_non_list_body_degrades_to_empty() -> None:
    # An instance returning an error object (not a list) must not crash the poll.
    url = "https://mastodon.social/api/v1/timelines/tag/crypto"
    respx.get(url).mock(return_value=httpx.Response(200, json={"error": "gone"}))
    provider = ms.MastodonProvider(instance="mastodon.social", hashtag="crypto")
    assert await provider.fetch() == []
    await provider.close()
