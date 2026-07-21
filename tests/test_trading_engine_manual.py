# tests/test_trading_engine_manual.py
import asyncio

from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken,
                             config.TradingConfig(trading_enabled=True))


def test_manual_order_whitelisted_places_order() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.manual_order(
        symbol="SOL", side="buy", order_type="market", quantity=1.0, price=None,
        issued_by="admin"))
    assert any(o["pair"] == "PF_SOLUSD" for o in kraken.orders)


def test_manual_order_unknown_symbol_rejected() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.manual_order(
        symbol="NOTACOIN", side="buy", order_type="market", quantity=1.0, price=None,
        issued_by="admin"))
    assert kraken.orders == []


def test_manual_order_blocked_by_kill_switch() -> None:
    cache = FakeCache(values={"trading:runtime": {"trading_enabled": False}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.manual_order(
        symbol="SOL", side="buy", order_type="market", quantity=1.0, price=None,
        issued_by="admin"))
    assert kraken.orders == []
