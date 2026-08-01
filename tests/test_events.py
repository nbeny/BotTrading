"""Event schema round-trip and discriminated-union parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cmi_common.events import (
    AnalysisEvent,
    PriceEvent,
    RiskApprovedEvent,
    parse_event,
)
from cmi_common.events.base import Source
from cmi_common.events.decision import Direction


def test_price_event_partition_key_is_symbol() -> None:
    e = PriceEvent(
        source=Source.COINGECKO,
        symbol="SOL",
        coin_id="solana",
        price_usd=Decimal("150"),
    )
    assert e.partition_key() == "SOL"


def test_roundtrip_parse_returns_concrete_type() -> None:
    original = AnalysisEvent(
        symbol="ETH", opportunity_score=82, confidence=0.78, reason="x"
    )
    decoded = parse_event(original.as_kafka_value())
    assert isinstance(decoded, AnalysisEvent)
    assert decoded.symbol == "ETH"
    assert decoded.opportunity_score == 82


def test_risk_approved_long_level_validation() -> None:
    ok = RiskApprovedEvent(
        symbol="SOL",
        direction=Direction.LONG,
        entry_price=150,
        stop_loss=142,
        take_profit=165,
        confidence=0.87,
    )
    assert ok.risk_reward_ratio == 0.0  # default until set by engine

    with pytest.raises(ValueError):
        RiskApprovedEvent(
            symbol="SOL",
            direction=Direction.LONG,
            entry_price=150,
            stop_loss=160,
            take_profit=165,
            confidence=0.5,  # SL above entry
        )


def test_score_bounds_enforced() -> None:
    with pytest.raises(ValueError):
        AnalysisEvent(symbol="X", opportunity_score=150, confidence=0.5, reason="r")


def test_new_social_sources_exist() -> None:
    from cmi_common.events.base import Source

    assert Source.BLUESKY.value == "bluesky"
    assert Source.RSS.value == "rss"
