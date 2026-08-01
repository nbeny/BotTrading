"""Collector source toggles held in Redis `collectors:runtime`."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "libs" / "cmi_common"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cmi_common.sources import runtime  # noqa: E402


class _FakeCache:
    def __init__(self, initial=None):
        self._store = {"collectors:runtime": initial} if initial is not None else {}

    async def get_json(self, key):
        return self._store.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._store[key] = value


async def test_default_all_enabled() -> None:
    cache = _FakeCache()  # no key -> everything on
    assert await runtime.is_enabled(cache, "social", "bluesky") is True
    assert await runtime.is_enabled(cache, "news", "gdelt") is True


async def test_category_toggle_off() -> None:
    cache = _FakeCache({"social_enabled": False})
    assert await runtime.is_enabled(cache, "social", "bluesky") is False
    assert await runtime.is_enabled(cache, "news", "gdelt") is True  # news still on


async def test_platform_toggle_off() -> None:
    cache = _FakeCache({"platforms": {"reddit": False}})
    assert await runtime.is_enabled(cache, "social", "reddit") is False
    assert await runtime.is_enabled(cache, "social", "bluesky") is True


async def test_set_runtime_merges_and_persists() -> None:
    cache = _FakeCache()
    out = await runtime.set_runtime(cache, {"platforms": {"cryptocompare": False}})
    assert out["platforms"]["cryptocompare"] is False
    assert out["platforms"]["gdelt"] is True  # untouched -> default on
    assert out["social_enabled"] is True
    # persisted so the poll loops see it
    assert await runtime.is_enabled(cache, "news", "cryptocompare") is False


async def test_get_runtime_exposes_known_platforms_shape() -> None:
    cache = _FakeCache()
    rt = await runtime.get_runtime(cache)
    assert "bluesky" in rt["platforms"] and "newsdata" in rt["platforms"]
