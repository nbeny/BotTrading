# tests/test_execution_event.py
"""ExecutionEvent schema + round-trip through parse_event."""
from __future__ import annotations

from cmi_common.events import parse_event
from cmi_common.events.execution import ExecutionEvent, ExecutionKind


def test_execution_event_roundtrip() -> None:
    ev = ExecutionEvent(
        kind=ExecutionKind.FILLED,
        symbol="SOL",
        risk_event_id="abc-123",
        kraken_order_id="OID-1",
        fill_price=150.5,
        size=2.0,
    )
    decoded = parse_event(ev.as_kafka_value())
    assert isinstance(decoded, ExecutionEvent)
    assert decoded.kind == ExecutionKind.FILLED
    assert decoded.symbol == "SOL"
    assert decoded.risk_event_id == "abc-123"
    assert decoded.partition_key() == "SOL"


def test_execution_rejected_carries_reason() -> None:
    ev = ExecutionEvent(
        kind=ExecutionKind.REJECTED, symbol="DOGE",
        risk_event_id="x", reason="unknown_symbol",
    )
    assert ev.reason == "unknown_symbol"
    assert ev.fill_price is None
