"""FourchanProvider: /biz/ catalog.json threads -> RawItem per crypto OP."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "fourchan_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "fourchan.py",
)
fc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = fc
_spec.loader.exec_module(fc)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _thread(no: int, com: str, replies: int = 0) -> dict:
    return {"no": no, "com": com, "replies": replies, "time": 1704196800}


@respx.mock
async def test_maps_threads_with_cashtags() -> None:
    respx.get(fc.CATALOG_URL).mock(return_value=httpx.Response(200, json=[
        {"page": 1, "threads": [
            _thread(101, "buy $BTC now<br>moon", replies=12),
            _thread(102, "generic no ticker thread"),
        ]},
    ]))
    provider = fc.FourchanProvider()
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    it = items[0]
    assert it.source == "fourchan"
    assert it.kind == "social"
    assert it.external_id == "101"
    assert it.symbols == ["BTC"]
    assert "$BTC" in it.text          # <br> stripped
    assert it.engagement == 12.0


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(fc.CATALOG_URL).mock(return_value=httpx.Response(429))
    provider = fc.FourchanProvider()
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
