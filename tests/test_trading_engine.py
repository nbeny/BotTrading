# tests/test_trading_engine.py
import asyncio

from tests.trading_helpers import load_module

from cmi_common.events.base import Source
from cmi_common.events.decision import Direction
from cmi_common.events.execution import ExecutionKind
from cmi_common.events.risk import RiskApprovedEvent


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

            async def srem(self, k, m):
                outer.sadd = [
                    (kk, mm) for (kk, mm) in outer.sadd if not (kk == k and mm == m)
                ]

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
        symbol="SOL",
        direction=Direction.LONG,
        entry_price=150.0,
        stop_loss=142.0,
        take_profit=165.0,
        confidence=0.8,
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
    ((_, ev),) = producer.published
    assert ev.kind == ExecutionKind.REJECTED
    assert ev.reason == "unknown_symbol"


def test_kill_switch_rejects() -> None:
    cache, producer, kraken = (
        FakeCache(values={"trading:runtime": {"trading_enabled": False}}),
        FakeProducer(),
        FakeKraken(),
    )
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert kraken.orders == []
    ((_, ev),) = producer.published
    assert ev.kind == ExecutionKind.REJECTED
    assert ev.reason == "kill_switch"


def test_signal_payload_matches_frontend_opportunity_contract() -> None:
    """`_signal_payload` is stored under `trading:pending:*` and returned
    verbatim by control-api's `list_pending`; the frontend renders it as
    `Opportunity`. This literal is the frontend's contract
    (frontend/src/lib/types/domain.ts) copied by hand -- it is the only thing
    holding the Python payload and the TS type in sync, since nothing else
    checks them against each other across the language boundary."""
    frontend_opportunity_fields = {
        "opportunity_id",
        "symbol",
        "direction",
        "opportunity_score",
        "confidence",
        "entry_price",
        "stop_loss",
        "take_profit",
        "position_size_pct",
        "risk_reward_ratio",
        "rationale",
        "key_risks",
        "ai_validated",
        "source",
        "status",
        "created_at",
    }
    mod = load_module("engine")
    sig = _signal(
        opportunity_score=79,
        rationale="Momentum breakout confirmed",
        key_risks=["Volatilite macro elevee"],
        ai_validated=True,
        risk_reward_ratio=2.0,
    )
    payload = mod._signal_payload(sig)

    assert frontend_opportunity_fields <= payload.keys()
    assert payload["opportunity_id"] == sig.event_id
    # 0..100 int -> 0..1 float; ScoreChip renders `score * 100`.
    assert payload["opportunity_score"] == 0.79
    assert payload["status"] == "pending"
    assert payload["rationale"] == "Momentum breakout confirmed"
    assert payload["key_risks"] == ["Volatilite macro elevee"]
    assert payload["ai_validated"] is True
    assert payload["risk_reward_ratio"] == 2.0


def test_signal_payload_defaults_unset_score_to_zero() -> None:
    """A RiskApprovedEvent built without opportunity_score (older producer /
    backward-compat default) must still produce a numeric field -- the TS
    contract does not allow undefined -- not raise or leak None through."""
    mod = load_module("engine")
    payload = mod._signal_payload(_signal())  # opportunity_score defaults None
    assert payload["opportunity_score"] == 0.0


def test_signal_payload_source_prefers_decision_provenance() -> None:
    """The mock -- the de-facto contract the frontend was built against --
    fills `Opportunity.source` with analysis provenance (frontend/src/lib/
    mock/store.ts: haiku_triage/sonnet_decision/manual), not the approving
    service. `event.source` on a RiskApprovedEvent is always risk-engine (the
    approver); `decision_source`, propagated from the DecisionEvent, is what
    must land in the payload."""
    mod = load_module("engine")
    sig = _signal(decision_source=Source.AI_SONNET)
    assert sig.source == Source.RISK_ENGINE  # sanity: approver, not analysis
    payload = mod._signal_payload(sig)
    assert payload["source"] == Source.AI_SONNET


def test_signal_payload_source_falls_back_when_decision_source_absent() -> None:
    """An older producer's event -- or a payload reconstructed from a partial
    Redis record -- never set `decision_source`. `_signal_payload` must still
    emit a usable, non-null `source` rather than leaking the field's None
    default through to the frontend."""
    mod = load_module("engine")
    sig = _signal()  # decision_source defaults None
    payload = mod._signal_payload(sig)
    assert payload["source"] == Source.RISK_ENGINE
    assert payload["source"] is not None


def test_idempotent_on_redelivery() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    sig = _signal()
    asyncio.run(engine.handle(sig))
    asyncio.run(engine.handle(sig))  # same event_id again
    assert len(kraken.orders) == 3  # not doubled
