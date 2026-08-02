import asyncio

from tests.test_trading_engine import FakeCache, FakeKraken, FakeProducer
from tests.trading_helpers import load_module

from cmi_common.events.execution import ExecutionKind


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(
        cache, producer, kraken, config.TradingConfig(trading_enabled=True)
    )


def _seed_pending(cache, event_id):
    asyncio.run(
        cache.set_json(
            f"trading:pending:{event_id}",
            {
                "symbol": "SOL",
                "direction": "long",
                "entry_price": 150.0,
                "stop_loss": 142.0,
                "take_profit": 165.0,
                "confidence": 0.8,
                "position_size_pct": 0.04,
                "correlation_id": "c1",
                "event_id": event_id,
            },
        )
    )
    asyncio.run(cache.client.sadd("trading:pending", event_id))


def test_approve_executes_and_clears_pending() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    _seed_pending(cache, "e1")
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.approve_opportunity("e1", issued_by="admin"))
    assert len(kraken.orders) == 3


def test_reject_emits_and_clears() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    _seed_pending(cache, "e1")
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.reject_opportunity("e1", reason="not now", issued_by="admin"))
    kinds = [ev.kind for _, ev in producer.published]
    assert ExecutionKind.REJECTED in kinds
