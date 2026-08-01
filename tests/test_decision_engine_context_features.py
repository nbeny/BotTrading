"""The engine reads the new features out of AnalysisEvent.meta."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

engine_mod = load_service_module("decision-engine", "engine")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


def _analysis(features: dict[str, Any]) -> AnalysisEvent:
    return AnalysisEvent(
        source=Source.AI_HAIKU,
        symbol="BTC",
        opportunity_score=80,
        confidence=0.9,
        reason="test",
        summary="",
        price_change_pct_24h=10.0,
        meta={"features": features},
    )


async def _breakdown(features: dict[str, Any]) -> dict[str, float]:
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis(features))
    _, event = producer.published[0]
    return event.meta["breakdown"]


async def test_funding_reaches_the_scored_features() -> None:
    assert "positioning" in await _breakdown({"funding_rate_8h": 0.001})


async def test_a_measured_zero_funding_still_creates_the_axis() -> None:
    # 0.0 is balanced positioning, measured. Dropping it would be the
    # unknown-vs-zero conflation arriving by the back door.
    assert "positioning" in await _breakdown({"funding_rate_8h": 0.0})


async def test_unlock_proximity_is_derived_from_the_timestamp() -> None:
    # The store carries an ISO date; the scorer wants days remaining. Deriving
    # it at read time keeps the stored value absolute and the score current.
    due = datetime.now(tz=UTC) + timedelta(days=3)
    breakdown = await _breakdown(
        {
            "has_unlock_schedule": True,
            "next_unlock_at": due.isoformat(),
            "next_unlock_pct_supply": 5.0,
        }
    )
    assert breakdown["fundamentals"] < 0.2


async def test_a_malformed_unlock_date_leaves_the_axis_unmeasured() -> None:
    # Known schedule, size known, date unreadable. It must not crash the
    # consumer loop — and it must not land on 1.0 either, which is what the
    # first version did: a known 5% unlock whose date failed to parse scored
    # the axis at its *maximum*, pulling the whole score up. Fabricated safety
    # is the same defect as a fabricated zero with the sign flipped.
    breakdown = await _breakdown(
        {
            "has_unlock_schedule": True,
            "next_unlock_at": "not-a-date",
            "next_unlock_pct_supply": 5.0,
        }
    )
    assert "fundamentals" not in breakdown


async def test_a_naive_unlock_date_is_read_as_utc_rather_than_crashing() -> None:
    # Nothing writes a naive timestamp today, but subtracting one from an aware
    # now() raises TypeError, which would kill the consumer loop.
    due = (datetime.now(tz=UTC) + timedelta(days=3)).replace(tzinfo=None)
    breakdown = await _breakdown(
        {
            "has_unlock_schedule": True,
            "next_unlock_at": due.isoformat(),
            "next_unlock_pct_supply": 5.0,
        }
    )
    assert breakdown["fundamentals"] < 0.2


async def test_a_past_unlock_date_clamps_to_zero_days() -> None:
    due = datetime.now(tz=UTC) - timedelta(days=2)
    breakdown = await _breakdown(
        {
            "has_unlock_schedule": True,
            "next_unlock_at": due.isoformat(),
            "next_unlock_pct_supply": 5.0,
        }
    )
    assert breakdown["fundamentals"] == 0.0


async def test_absent_context_features_leave_the_axes_out() -> None:
    breakdown = await _breakdown({})
    assert "positioning" not in breakdown
    assert "fundamentals" not in breakdown
