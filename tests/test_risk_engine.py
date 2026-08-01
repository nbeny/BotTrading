"""Risk engine: RiskApprovedEvent must carry the opportunity metadata the
frontend needs, propagated from the DecisionEvent being approved — not left at
RiskApprovedEvent's own (unset) defaults."""

from __future__ import annotations

import asyncio

from tests.service_modules import load_service_module

from cmi_common.events.base import Source
from cmi_common.events.decision import DecisionEvent, Direction
from cmi_common.events.risk import RiskApprovedEvent


class FakeCache:
    def __init__(self, exposure: float = 0.0, blacklisted: bool = False) -> None:
        self._exposure = exposure
        self._blacklisted = blacklisted
        self.set_calls: list[tuple[str, object]] = []

    async def get_json(self, key):
        if key == "risk:exposure":
            return self._exposure
        return None

    async def set_json(self, key, value, ttl_seconds=0):
        self.set_calls.append((key, value))

    @property
    def client(self):
        outer = self

        class _C:
            async def sismember(self, key, member):
                return outer._blacklisted

        return _C()


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[object, object]] = []

    async def publish(self, topic, event):
        self.published.append((topic, event))


def _decision(**kw) -> DecisionEvent:
    base = {
        "symbol": "SOL",
        "direction": Direction.LONG,
        "opportunity_score": 79,
        "confidence": 0.8,
        "suggested_entry_price": 150.0,
        "rationale": "Momentum breakout confirmed",
        "key_risks": ["Volatilite macro elevee"],
        "ai_validated": True,
    }
    base.update(kw)
    return DecisionEvent(**base)


def _engine(cache, producer):
    engine_mod = load_service_module("risk-engine", "engine")
    rules_mod = load_service_module("risk-engine", "rules")
    return engine_mod.RiskEngine(cache, producer, rules_mod.RiskConfig())


def test_approval_propagates_opportunity_fields_from_decision() -> None:
    cache, producer = FakeCache(), FakeProducer()
    engine = _engine(cache, producer)
    asyncio.run(engine.handle(_decision()))

    ((_, approved),) = producer.published
    assert isinstance(approved, RiskApprovedEvent)
    # These must come from the DecisionEvent, not RiskApprovedEvent's own
    # (unset) defaults -- that is exactly the bug this test guards against.
    assert approved.opportunity_score == 79
    assert approved.rationale == "Momentum breakout confirmed"
    assert approved.key_risks == ["Volatilite macro elevee"]
    assert approved.ai_validated is True
    # The approval's own `source` is risk-engine (the approving service); the
    # analysis provenance -- what the frontend's `Opportunity.source` means --
    # travels separately, on `decision_source`, copied from the DecisionEvent.
    assert approved.source == Source.RISK_ENGINE
    assert approved.decision_source == Source.AI_SONNET


def test_rejected_decision_does_not_publish_approval() -> None:
    """Sanity check the fixture: a blacklisted symbol still rejects, so the
    propagation test above is actually exercising the approval path."""
    cache, producer = FakeCache(blacklisted=True), FakeProducer()
    engine = _engine(cache, producer)
    asyncio.run(engine.handle(_decision()))

    assert len(producer.published) == 1
    _topic, rejected = producer.published[0]
    assert rejected.event_type == "RiskRejectedEvent"
