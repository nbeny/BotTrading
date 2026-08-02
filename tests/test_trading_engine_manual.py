# tests/test_trading_engine_manual.py
import asyncio

from tests.test_trading_engine import FakeCache, FakeKraken, FakeProducer
from tests.trading_helpers import load_module


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(
        cache, producer, kraken, config.TradingConfig(trading_enabled=True)
    )


def test_manual_order_whitelisted_places_order() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(
        engine.manual_order(
            symbol="SOL",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=None,
            issued_by="admin",
        )
    )
    assert any(o["pair"] == "PF_SOLUSD" for o in kraken.orders)


def test_manual_order_unknown_symbol_rejected() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(
        engine.manual_order(
            symbol="NOTACOIN",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=None,
            issued_by="admin",
        )
    )
    assert kraken.orders == []


def test_manual_order_blocked_by_kill_switch() -> None:
    cache = FakeCache(values={"trading:runtime": {"trading_enabled": False}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(
        engine.manual_order(
            symbol="SOL",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=None,
            issued_by="admin",
        )
    )
    assert kraken.orders == []


def test_manual_order_pair_notation_is_normalized() -> None:
    # UI sends "SOL/USDT"; the whitelist is keyed by the base ticker.
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(
        engine.manual_order(
            symbol="SOL/USDT",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=None,
            issued_by="admin",
        )
    )
    assert any(o["pair"] == "PF_SOLUSD" for o in kraken.orders)


def test_manual_market_order_over_cap_rejected_via_feature_price() -> None:
    # market order, no explicit price; ref price resolved from features:{base}.
    # 1 * 100_000 = 100_000 notional >> default MAX_ORDER_USD (500) -> rejected.
    cache = FakeCache(values={"features:BTC": {"price": 100_000.0}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(
        engine.manual_order(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=None,
            issued_by="admin",
        )
    )
    assert kraken.orders == []


def test_manual_market_order_under_cap_placed() -> None:
    cache = FakeCache(values={"features:SOL": {"price": 150.0}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(
        engine.manual_order(
            symbol="SOL",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=None,
            issued_by="admin",
        )
    )
    assert any(o["pair"] == "PF_SOLUSD" for o in kraken.orders)
