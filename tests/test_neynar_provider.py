"""NeynarProvider: Farcaster cast search -> RawItem per crypto cast."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "neynar_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-social"
    / "app"
    / "providers"
    / "neynar.py",
)
ny = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = ny
_spec.loader.exec_module(ny)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _cast(h: str, text: str, likes: int = 0) -> dict:
    return {
        "hash": h,
        "text": text,
        "timestamp": "2024-01-02T12:00:00.000Z",
        "author": {"username": "alice", "fid": 1},
        "reactions": {"likes_count": likes, "recasts_count": 0},
        "replies": {"count": 0},
    }


@respx.mock
async def test_maps_every_cast_and_leaves_symbols_to_the_normalizer() -> None:
    # Requiring an explicit $TICKER discarded every cast, so Farcaster produced
    # zero rows despite a working API key. The collector's normalizer resolves
    # symbols for all providers and overwrites whatever is set here.
    respx.get(ny.SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "casts": [
                        _cast("0xaa", "$BTC looking strong", likes=5),
                        _cast("0xbb", "no ticker here"),
                    ]
                }
            },
        )
    )
    provider = ny.NeynarProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 2
    it = items[0]
    assert it.source == "neynar"
    assert it.kind == "social"
    assert it.external_id == "0xaa"
    assert it.symbols == []
    assert it.author == "alice"
    assert it.engagement == 5.0
    assert items[1].external_id == "0xbb"


async def test_no_key_is_noop() -> None:
    provider = ny.NeynarProvider(None)
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(ny.SEARCH_URL).mock(return_value=httpx.Response(429))
    provider = ny.NeynarProvider("KEY")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


@respx.mock
async def test_null_result_degrades_to_empty() -> None:
    respx.get(ny.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"result": None})
    )
    provider = ny.NeynarProvider("KEY")
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_null_item_fields_do_not_crash() -> None:
    cast = _cast("0xaa", "$BTC up", likes=2)
    cast["reactions"] = None
    cast["replies"] = None
    cast["author"] = None
    respx.get(ny.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"result": {"casts": [cast, None]}})
    )
    provider = ny.NeynarProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    assert items[0].external_id == "0xaa"
    assert items[0].engagement == 0.0
    assert items[0].author is None
