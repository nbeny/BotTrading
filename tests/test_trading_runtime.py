# tests/test_trading_runtime.py
import asyncio

from tests.trading_helpers import load_module


class FakeCache:
    def __init__(self, values=None):
        self._values = dict(values or {})
        self.sets = {}

    async def get_json(self, key):
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value
        self.sets[key] = value


def _mods():
    return load_module("runtime"), load_module("config")


def test_load_returns_defaults_when_absent() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig(max_order_usd=500.0)
    cache = FakeCache()
    eff = asyncio.run(runtime.RuntimeConfig.load(cache, defaults))
    assert eff.max_order_usd == 500.0
    assert eff.mode == config.Mode.DRY_RUN


def test_redis_overlay_wins() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig(max_order_usd=500.0, trading_enabled=True)
    cache = FakeCache(values={"trading:runtime": {
        "mode": "demo", "trading_enabled": False, "max_order_usd": 250.0,
    }})
    eff = asyncio.run(runtime.RuntimeConfig.load(cache, defaults))
    assert eff.mode == config.Mode.DEMO
    assert eff.trading_enabled is False
    assert eff.max_order_usd == 250.0
    # unspecified fields keep defaults
    assert eff.max_leverage == defaults.max_leverage


def test_write_defaults_only_if_absent() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig()
    cache = FakeCache()
    asyncio.run(runtime.RuntimeConfig.write_defaults_if_absent(cache, defaults))
    assert "trading:runtime" in cache.sets
    # second call must not overwrite an operator-modified value
    cache._values["trading:runtime"]["mode"] = "live"
    asyncio.run(runtime.RuntimeConfig.write_defaults_if_absent(cache, defaults))
    assert cache._values["trading:runtime"]["mode"] == "live"


def test_set_field_updates_runtime() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig()
    cache = FakeCache()
    asyncio.run(runtime.RuntimeConfig.write_defaults_if_absent(cache, defaults))
    asyncio.run(runtime.RuntimeConfig.set_fields(cache, {"auto_trading_enabled": False}))
    assert cache._values["trading:runtime"]["auto_trading_enabled"] is False
