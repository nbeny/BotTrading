# tests/test_trading_control.py
import asyncio

from cmi_common.events.control import ControlCommand, ControlCommandEvent
from tests.trading_helpers import load_module


class FakeCache:
    def __init__(self, values=None):
        self._values = dict(values or {})

    async def get_json(self, key):
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value


def _handler(cache):
    control = load_module("control")
    config = load_module("config")
    # kraken/engine not needed for settings commands; pass None.
    return control.ControlHandler(cache, engine=None, kraken=None,
                                  defaults=config.TradingConfig())


def _cmd(command, **payload):
    return ControlCommandEvent(command=command, payload=payload, issued_by="admin")


def test_set_mode_writes_runtime() -> None:
    cache = FakeCache()
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_MODE, mode="demo")))
    assert cache._values["trading:runtime"]["mode"] == "demo"


def test_set_kill_switch() -> None:
    cache = FakeCache()
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_KILL_SWITCH, enabled=False)))
    assert cache._values["trading:runtime"]["trading_enabled"] is False


def test_set_auto_trading() -> None:
    cache = FakeCache()
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_AUTO_TRADING, enabled=False)))
    assert cache._values["trading:runtime"]["auto_trading_enabled"] is False


def test_set_caps_partial() -> None:
    cache = FakeCache(values={"trading:runtime": {"max_order_usd": 500.0, "max_leverage": 3.0}})
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_CAPS, max_order_usd=250.0)))
    assert cache._values["trading:runtime"]["max_order_usd"] == 250.0
    assert cache._values["trading:runtime"]["max_leverage"] == 3.0
