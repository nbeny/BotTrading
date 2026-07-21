import asyncio

from tests.trading_helpers import load_module


class FakeCache:
    """Minimal async Cache stand-in: get + allow."""
    def __init__(self, values=None, allow=True):
        self._values = values or {}
        self._allow = allow
        self.allow_calls = []

    async def get_json(self, key):
        return self._values.get(key)

    async def allow(self, key, limit, window_seconds):
        self.allow_calls.append((key, limit, window_seconds))
        return self._allow


def _cfg(mode_mod, **kw):
    return mode_mod.TradingConfig(**kw)


def test_kill_switch_env_blocks() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache()
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=False))
    )
    assert reason == "kill_switch"


def test_kill_switch_redis_blocks() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache(values={"trading:enabled": False})
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=True))
    )
    assert reason == "kill_switch"


def test_rate_limit_blocks() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache(allow=False)
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=True, max_orders_per_hour=10))
    )
    assert reason == "rate_limit"
    assert cache.allow_calls == [("trading:orders", 10, 3600)]


def test_all_clear_returns_none() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache(allow=True)
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=True))
    )
    assert reason is None
