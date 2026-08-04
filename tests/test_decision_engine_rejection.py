"""Below-threshold analyses leave an audit trail instead of vanishing.

The deterministic path was the one pipeline stage whose rejections were
invisible: a bare `return` under the threshold. The funnel cannot report where
signals die if a stage drops them silently.
"""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

de = load_service_module("decision-engine", "engine")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic, event) -> None:
        self.published.append((topic, event))


async def test_low_score_publishes_a_rejection() -> None:
    producer = FakeProducer()
    engine = de.DecisionEngine(producer, decision_threshold=70)
    await engine.handle(
        AnalysisEvent(symbol="BTC", opportunity_score=22, confidence=0.6, reason="r")
    )
    assert len(producer.published) == 1
    topic, event = producer.published[0]
    assert topic is Topic.DECISION
    assert event.event_type == "RiskRejectedEvent"
    assert event.symbol == "BTC"
    assert event.source == Source.DECISION_ENGINE


async def test_rejection_reason_names_both_the_score_and_the_threshold() -> None:
    """A reason of "below threshold" is useless for tuning: the operator needs
    to know how far below, and what the bar currently is."""
    producer = FakeProducer()
    engine = de.DecisionEngine(producer, decision_threshold=70)
    await engine.handle(
        AnalysisEvent(symbol="BTC", opportunity_score=22, confidence=0.6, reason="r")
    )
    _topic, event = producer.published[0]
    assert "70" in event.reason
    assert "below decision threshold" in event.reason


async def test_rejection_preserves_the_correlation_id() -> None:
    """The trace view stitches a signal's lineage by correlation_id; a rejection
    that drops it cannot be linked back to the price tick that caused it."""
    producer = FakeProducer()
    engine = de.DecisionEngine(producer, decision_threshold=70)
    analysis = AnalysisEvent(
        symbol="BTC", opportunity_score=22, confidence=0.6, reason="r"
    )
    await engine.handle(analysis)
    _topic, event = producer.published[0]
    assert event.correlation_id == analysis.correlation_id


async def test_high_score_still_publishes_a_decision() -> None:
    producer = FakeProducer()
    engine = de.DecisionEngine(producer, decision_threshold=10)
    await engine.handle(
        AnalysisEvent(
            symbol="BTC",
            opportunity_score=90,
            confidence=0.9,
            reason="r",
            price_change_pct_24h=14.0,
            volume_spike_ratio=4.0,
            sentiment_score=0.9,
            # The engine reads only event.meta["features"] (features_from), so
            # the scored fields must ride there too, not just top-level.
            meta={
                "features": {
                    "price_change_pct_24h": 14.0,
                    "volume_spike_ratio": 4.0,
                    "sentiment_score": 0.9,
                }
            },
        )
    )
    assert len(producer.published) == 1
    _topic, event = producer.published[0]
    assert event.event_type == "DecisionEvent"
