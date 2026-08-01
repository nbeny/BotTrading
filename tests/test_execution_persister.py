"""ExecutionEvent maps to the Trade fields the persister should update."""

from cmi_common.events.execution import ExecutionEvent, ExecutionKind


def test_execution_event_carries_update_fields() -> None:
    ev = ExecutionEvent(
        kind=ExecutionKind.FILLED,
        symbol="SOL",
        risk_event_id="rk-1",
        kraken_order_id="OID-9",
        fill_price=151.2,
        size=2.0,
    )
    # These are exactly the columns the persister writes to the trades row.
    assert ev.risk_event_id == "rk-1"  # WHERE trades.event_id = risk_event_id
    assert ev.kraken_order_id == "OID-9"
    assert ev.fill_price == 151.2
    # BaseEvent uses use_enum_values, so kind is already the string value.
    assert ev.kind == "filled"  # -> trades.status
