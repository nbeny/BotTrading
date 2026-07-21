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


class FakeEngine:
    def __init__(self):
        self.closed = []
        self.adjusted = []

    async def close_position(self, event_id, *, issued_by=None):
        self.closed.append((event_id, issued_by))

    async def adjust_sltp(self, event_id, *, stop_loss=None, take_profit=None, issued_by=None):
        self.adjusted.append((event_id, stop_loss, take_profit, issued_by))


def _handler_with_engine(cache, engine):
    control = load_module("control")
    config = load_module("config")
    return control.ControlHandler(cache, engine=engine, kraken=None,
                                  defaults=config.TradingConfig())


def test_close_position_dispatches_to_engine() -> None:
    engine = FakeEngine()
    asyncio.run(_handler_with_engine(FakeCache(), engine).handle(
        _cmd(ControlCommand.CLOSE_POSITION, event_id="e1")))
    assert engine.closed == [("e1", "admin")]


def test_adjust_sltp_dispatches_to_engine() -> None:
    engine = FakeEngine()
    asyncio.run(_handler_with_engine(FakeCache(), engine).handle(
        _cmd(ControlCommand.ADJUST_SLTP, event_id="e1", stop_loss=140.0)))
    assert engine.adjusted == [("e1", 140.0, None, "admin")]


def test_approve_reject_dispatch() -> None:
    control = load_module("control")
    config = load_module("config")

    class E:
        def __init__(self): self.calls = []
        async def approve_opportunity(self, event_id, *, issued_by=None):
            self.calls.append(("approve", event_id, issued_by))
        async def reject_opportunity(self, event_id, *, reason="operator_reject", issued_by=None):
            self.calls.append(("reject", event_id, reason, issued_by))

    e = E()
    h = control.ControlHandler(FakeCache(), engine=e, kraken=None, defaults=config.TradingConfig())
    asyncio.run(h.handle(_cmd(ControlCommand.APPROVE_OPPORTUNITY, event_id="e1")))
    asyncio.run(h.handle(_cmd(ControlCommand.REJECT_OPPORTUNITY, event_id="e2", reason="no")))
    assert ("approve", "e1", "admin") in e.calls
    assert ("reject", "e2", "no", "admin") in e.calls


def test_manual_order_dispatch() -> None:
    control = load_module("control")
    config = load_module("config")

    class E:
        def __init__(self): self.calls = []
        async def manual_order(self, *, symbol, side, order_type, quantity, price=None, issued_by=None):
            self.calls.append((symbol, side, order_type, quantity, price, issued_by))

    e = E()
    h = control.ControlHandler(FakeCache(), engine=e, kraken=None, defaults=config.TradingConfig())
    asyncio.run(h.handle(_cmd(ControlCommand.MANUAL_ORDER, symbol="SOL", side="buy",
                              order_type="market", quantity=1.0)))
    assert e.calls == [("SOL", "buy", "market", 1.0, None, "admin")]
