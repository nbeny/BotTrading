import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken, _signal


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken,
                             config.TradingConfig(trading_enabled=True))


def test_auto_off_queues_pending_no_orders() -> None:
    cache = FakeCache(values={"trading:runtime": {"auto_trading_enabled": False,
                                                  "trading_enabled": True}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert kraken.orders == []
    (_, ev), = producer.published
    assert ev.kind == ExecutionKind.PENDING
    # pending payload stored
    assert cache._values.get("trading:pending:" + _signal().event_id) is None  # different id
    assert any(k.startswith("trading:pending:") for k in cache._values)


def test_auto_on_executes() -> None:
    cache = FakeCache(values={"trading:runtime": {"auto_trading_enabled": True,
                                                  "trading_enabled": True}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert len(kraken.orders) == 3  # entry + sl + tp
