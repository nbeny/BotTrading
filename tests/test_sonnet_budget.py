"""Sonnet worker LLM budget + per-symbol cooldown gate.

Loaded via the shared helper so the module keeps a parent package: worker.py
imports its siblings relatively, which a bare spec_from_file_location cannot
resolve.
"""

from __future__ import annotations

from service_modules import load_service_module

SonnetWorker = load_service_module("ai-worker-sonnet", "worker").SonnetWorker


class _FakeCache:
    def __init__(self) -> None:
        self._cd: dict[str, object] = {}
        self._used = 0

    async def get_json(self, key):
        return self._cd.get(key)

    async def allow(self, key, limit, window):
        self._used += 1
        return self._used <= limit

    async def set_json(self, key, value, ttl_seconds=60):
        self._cd[key] = value


def _worker(cache, cap):
    return SonnetWorker(
        claude=None, producer=None, cache=cache, max_calls_per_hour=cap, symbol_cooldown_s=900
    )


async def test_cooldown_blocks_same_symbol() -> None:
    w = _worker(_FakeCache(), cap=10)
    assert await w._may_call("BTC") is True
    assert await w._may_call("BTC") is False


async def test_hourly_budget_ceiling() -> None:
    w = _worker(_FakeCache(), cap=2)
    assert await w._may_call("BTC") is True
    assert await w._may_call("ETH") is True
    assert await w._may_call("SOL") is False
