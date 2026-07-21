# tests/test_trading_engine.py
import asyncio

from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.events.risk import RiskApprovedEvent
from cmi_common.events.decision import Direction
from tests.trading_helpers import load_module


class FakeCache:
    def __init__(self, values=None, allow=True):
        self._values = dict(values or {})
        self._allow = allow
        self.sets = {}
        self.sadd = []

    async def get_json(self, key):
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value
        self.sets[key] = value

    async def allow(self, key, limit, window_seconds):
        return self._allow

    @property
    def client(self):
        outer = self

        class _C:
            async def sismember(self, k, m):
                return False

            async def sadd(self, k, m):
                outer.sadd.append((k, m))

            async def hset(self, *a, **k):
                return None
        return _C()


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append((topic, event))


class FakeKraken:
    def __init__(self, positions=None):
        self.orders = []
        self._equity = 10_000.0

    async def get_accounts(self):
        return {"accounts": {"flex": {"portfolioValue": self._equity}}}

    async def send_order(self, **kw):
        self.orders.append(kw)
        return {"result": "success", "order_id": f"OID-{len(self.orders)}"}

    async def cancel_order(self, **kw):
        return {"result": "success"}


def _signal(**kw):
    base = dict(
        symbol="SOL", direction=Direction.LONG, entry_price=150.0,
        stop_loss=142.0, take_profit=165.0, confidence=0.8,
        position_size_pct=0.04,
    )
    base.update(kw)
    return RiskApprovedEvent(**base)


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config_mod = load_module("config")
    cfg = config_mod.TradingConfig(trading_enabled=True)
    return mod.TradingEngine(cache, producer, kraken, cfg)


def test_happy_path_places_entry_and_bracket() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    # entry + stop + take-profit = 3 orders
    assert len(kraken.orders) == 3
    kinds = [t[1].kind for t in producer.published]
    assert ExecutionKind.SUBMITTED in kinds
    assert ExecutionKind.FILLED in kinds


def test_unknown_symbol_is_rejected_not_traded() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal(symbol="NOTACOIN")))
    assert kraken.orders == []
    (_, ev), = producer.published
    assert ev.kind == ExecutionKind.REJECTED
    assert ev.reason == "unknown_symbol"


def test_kill_switch_rejects() -> None:
    cache, producer, kraken = FakeCache(values={"trading:enabled": False}), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert kraken.orders == []
    (_, ev), = producer.published
    assert ev.kind == ExecutionKind.REJECTED
    assert ev.reason == "kill_switch"


def test_idempotent_on_redelivery() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    sig = _signal()
    asyncio.run(engine.handle(sig))
    asyncio.run(engine.handle(sig))  # same event_id again
    assert len(kraken.orders) == 3  # not doubled
