# tests/test_trading_engine_actions.py
import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken, config.TradingConfig(trading_enabled=True))


def _seed_position(cache):
    # mirror what engine.handle stores on fill
    asyncio.run(cache.set_json("trading:position:e1", {
        "symbol": "SOL", "pair": "PF_SOLUSD", "side": "buy", "size": 2.0,
        "entry_price": 150.0, "position_size_pct": 0.04,
    }))


def test_close_position_sends_reduce_only_market() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    _seed_position(cache)
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.close_position("e1", issued_by="admin"))
    # a reduce-only market order on the opposite side (sell) was sent
    assert any(o["order_type"] == "mkt" and o["reduce_only"] and o["side"] == "sell"
               for o in kraken.orders)


def test_close_unknown_position_is_noop_reject() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.close_position("missing", issued_by="admin"))
    assert kraken.orders == []
