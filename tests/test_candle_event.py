"""CandleEvent round-trips through the shared registry like every event."""

from __future__ import annotations

from decimal import Decimal

from cmi_common.events import parse_event
from cmi_common.events.base import Source
from cmi_common.events.market import CandleEvent
from cmi_common.kafka import Topic


def test_candle_event_roundtrip() -> None:
    e = CandleEvent(
        source=Source.KRAKEN,
        symbol="BTC",
        interval="1h",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("12.5"),
    )
    decoded = parse_event(e.model_dump_json())
    assert isinstance(decoded, CandleEvent)
    assert decoded.vwap is None  # absent ≠ 0
    assert decoded.trades is None
    assert decoded.venue == "kraken"
    assert e.partition_key() == "BTC"
    assert Topic.CANDLES.value == "market.candle.events"
