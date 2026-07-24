"""LensProvider: GraphQL posts search -> RawItem per crypto publication."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "lens_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-social"
    / "app"
    / "providers"
    / "lens.py",
)
ln = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = ln
_spec.loader.exec_module(ln)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _post(pid: str, content: str, comments: int = 0) -> dict:
    return {
        "id": pid,
        "timestamp": "2024-01-02T12:00:00Z",
        "author": {"username": {"value": "lens/alice"}},
        "stats": {"comments": comments, "reposts": 0, "upvotes": 0},
        "metadata": {"content": content},
    }


@respx.mock
async def test_maps_posts_with_cashtags() -> None:
    respx.post(ln.GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "posts": {
                        "items": [
                            _post("0x01", "$BTC to the moon", comments=3),
                            _post("0x02", "gm no ticker"),
                        ]
                    }
                }
            },
        )
    )
    provider = ln.LensProvider()
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    it = items[0]
    assert it.source == "lens"
    assert it.kind == "social"
    assert it.external_id == "0x01"
    assert it.symbols == ["BTC"]
    assert it.author == "lens/alice"
    assert it.engagement == 3.0


@respx.mock
async def test_graphql_errors_return_empty() -> None:
    respx.post(ln.GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "bad query"}]})
    )
    provider = ln.LensProvider()
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.post(ln.GRAPHQL_URL).mock(return_value=httpx.Response(429))
    provider = ln.LensProvider()
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


@respx.mock
async def test_null_data_degrades_to_empty() -> None:
    respx.post(ln.GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json={"data": None})
    )
    provider = ln.LensProvider()
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_null_post_in_items_is_skipped() -> None:
    respx.post(ln.GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"posts": {"items": [None, _post("0x09", "$ETH", comments=1)]}}
            },
        )
    )
    provider = ln.LensProvider()
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    assert items[0].external_id == "0x09"
