"""collector-social wiring: seeding the Telegram channel list on first boot.

The seed is the one place where the env var and the operator's list can fight.
``_seed_telegram_channels`` reads the *raw* Redis key rather than ``get_runtime``
precisely so an absent entry (never configured) stays distinguishable from an
explicit ``[]`` (configured to poll nobody) — ``get_runtime`` fills the first in
from the seed, which would make every restart re-apply the env var over the
operator's edits. That invariant has no test, so it lives here.
"""

from __future__ import annotations

from typing import Any

import pytest
from service_modules import load_service_module

main = load_service_module("collector-social", "main")

RUNTIME_KEY = "collectors:runtime"


class _FakeCache:
    """Stand-in for cmi_common.cache.Cache; counts writes.

    The write count is what separates "seeded with the same value" from "left
    alone" — the two are indistinguishable by the stored payload alone when the
    env var happens to match what is already there.
    """

    def __init__(self, initial: Any | None = None) -> None:
        self._store: dict[str, Any] = {} if initial is None else {RUNTIME_KEY: initial}
        self.writes = 0

    async def get_json(self, key: str) -> Any:
        return self._store.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self._store[key] = value
        self.writes += 1

    def runtime(self) -> Any:
        return self._store.get(RUNTIME_KEY)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHANNELS", "@FromEnv, t.me/alsoFromEnv")


async def test_an_absent_key_is_seeded_from_the_env_var() -> None:
    cache = _FakeCache()

    await main._seed_telegram_channels(cache)

    assert cache.runtime()["telegram_channels"] == ["fromenv", "alsofromenv"]


async def test_a_deliberately_empty_list_survives_the_restart() -> None:
    # `[]` is the operator saying "poll nobody". Re-seeding it would restart
    # twenty-four channels they had just switched off, silently.
    cache = _FakeCache({"telegram_channels": []})

    await main._seed_telegram_channels(cache)

    assert cache.runtime()["telegram_channels"] == []
    assert cache.writes == 0  # not "written back identical" — not written at all


async def test_the_env_var_never_overrides_a_configured_list() -> None:
    cache = _FakeCache({"telegram_channels": ["operatorschoice"]})

    await main._seed_telegram_channels(cache)

    assert cache.runtime()["telegram_channels"] == ["operatorschoice"]
    assert cache.writes == 0


async def test_seeding_leaves_the_other_runtime_fields_alone() -> None:
    # The key is shared with the source toggles: seeding must not be the reason
    # a collector the operator disabled comes back on.
    cache = _FakeCache({"social_enabled": False, "platforms": {"reddit": False}})

    await main._seed_telegram_channels(cache)

    runtime = cache.runtime()
    assert runtime["telegram_channels"] == ["fromenv", "alsofromenv"]
    assert runtime["social_enabled"] is False
    assert runtime["platforms"]["reddit"] is False
    assert runtime["platforms"]["telegram"] is True  # untouched -> default on
