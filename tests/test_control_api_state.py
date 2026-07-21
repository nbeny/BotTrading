import asyncio

from tests.control_api_helpers import load_module


class FakeCache:
    def __init__(self, values):
        self._values = values

    async def get_json(self, key):
        return self._values.get(key)


def test_read_settings_returns_runtime() -> None:
    state = load_module("state")
    cache = FakeCache({"trading:runtime": {"mode": "demo", "trading_enabled": True,
                                           "auto_trading_enabled": False,
                                           "max_order_usd": 500.0}})
    reader = state.StateReader(cache, db=None)
    settings = asyncio.run(reader.settings())
    assert settings["mode"] == "demo"
    assert settings["auto_trading_enabled"] is False


def test_read_settings_empty_when_absent() -> None:
    state = load_module("state")
    reader = state.StateReader(FakeCache({}), db=None)
    assert asyncio.run(reader.settings()) == {}
