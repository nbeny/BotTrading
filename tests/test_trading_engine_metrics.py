"""trading-engine must report what it consumes and produces.

Without these counters the `execute` stage of the Command Center graph has no
throughput to show, and an operator cannot tell a stopped executor from a quiet
one.
"""

from __future__ import annotations

from prometheus_client import REGISTRY

from cmi_common.events import RiskApprovedEvent
from cmi_common.kafka import Topic
from service_modules import load_service_module

engine_mod = load_service_module("trading-engine", "engine")


def _value(metric: str, **labels) -> float:
    return REGISTRY.get_sample_value(metric, labels) or 0.0


def _risk_event() -> RiskApprovedEvent:
    return RiskApprovedEvent(
        symbol="BTC",
        direction="long",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        confidence=0.9,
        position_size_pct=0.05,
        correlation_id="cid-1",
    )


class _Cache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get_json(self, key):
        return self.store.get(key)


async def test_consumed_counter_increments_on_a_duplicate_event() -> None:
    """A duplicate is still an event the engine consumed: counting it after the
    idempotency check would under-report exactly when redelivery spikes."""
    cache = _Cache()
    event = _risk_event()
    cache.store[engine_mod.SUBMITTED_KEY.format(event_id=event.event_id)] = {"ok": True}
    eng = engine_mod.TradingEngine(cache, None, None, None)

    labels = dict(
        service="trading-engine",
        topic=Topic.RISK_APPROVED.value,
        event_type=event.event_type,
    )
    before = _value("cmi_events_consumed_total", **labels)
    await eng.handle(event)
    assert _value("cmi_events_consumed_total", **labels) == before + 1
