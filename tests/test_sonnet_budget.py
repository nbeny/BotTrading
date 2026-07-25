"""Sonnet worker LLM budget + per-symbol cooldown gate.

Loaded via importlib under a unique name to avoid shadowing the `app` package of
other services during a full-suite run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "libs" / "cmi_common"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

_PATH = (
    Path(__file__).resolve().parents[1]
    / "services" / "ai-worker-sonnet" / "app" / "worker.py"
)
_spec = importlib.util.spec_from_file_location("sonnet_worker_mod", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["sonnet_worker_mod"] = _mod
_spec.loader.exec_module(_mod)
SonnetWorker = _mod.SonnetWorker


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
