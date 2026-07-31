"""The two new market events survive a Kafka round-trip and are routable."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from cmi_common.events import (
    DerivativesEvent,
    FundamentalsEvent,
    parse_event,
)
from cmi_common.events.base import Source
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_derivatives_event_round_trips_through_parse_event() -> None:
    event = DerivativesEvent(
        source=Source.BINANCE_FUTURES,
        symbol="BTC",
        funding_rate_8h=0.0001,
        funding_annualized_pct=10.95,
        open_interest_usd=Decimal("1000000"),
        open_interest_change_pct_24h=5.0,
        long_short_account_ratio=1.8,
    )
    decoded = parse_event(event.as_kafka_value())
    assert isinstance(decoded, DerivativesEvent)
    assert decoded.symbol == "BTC"
    assert decoded.funding_rate_8h == 0.0001
    assert decoded.open_interest_usd == Decimal("1000000")


def test_fundamentals_event_round_trips_through_parse_event() -> None:
    event = FundamentalsEvent(
        source=Source.DEFILLAMA,
        symbol="AAVE",
        coin_id="aave",
        tvl_usd=Decimal("5000000"),
        tvl_change_pct_7d=3.5,
        next_unlock_at=datetime(2026, 8, 15, tzinfo=UTC),
        next_unlock_pct_supply=2.5,
        has_unlock_schedule=True,
    )
    decoded = parse_event(event.as_kafka_value())
    assert isinstance(decoded, FundamentalsEvent)
    assert decoded.has_unlock_schedule is True
    assert decoded.next_unlock_pct_supply == 2.5
    assert decoded.next_unlock_at == datetime(2026, 8, 15, tzinfo=UTC)


def test_events_partition_by_symbol() -> None:
    assert (
        DerivativesEvent(source=Source.BINANCE_FUTURES, symbol="ETH").partition_key()
        == "ETH"
    )
    assert (
        FundamentalsEvent(
            source=Source.DEFILLAMA, symbol="ETH", coin_id="ethereum"
        ).partition_key()
        == "ETH"
    )


def test_new_topics_are_registered_everywhere() -> None:
    # A topic missing from TOPIC_EVENT or TOPIC_PARTITIONS publishes fine and
    # fails later, which is how JournalEntryEvent shipped broken.
    for topic in (Topic.DERIVATIVES, Topic.FUNDAMENTALS):
        assert topic in TOPIC_EVENT
        assert topic in TOPIC_PARTITIONS
    assert TOPIC_EVENT[Topic.DERIVATIVES] is DerivativesEvent
    assert TOPIC_EVENT[Topic.FUNDAMENTALS] is FundamentalsEvent


def test_a_measured_zero_and_an_absent_field_stay_distinct() -> None:
    # Neutral funding is a reading; an unfetched ratio is not. If these ever
    # collapse onto the same value the positioning axis starts scoring symbols
    # on data nobody collected.
    decoded = parse_event(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0
        ).as_kafka_value()
    )
    assert decoded.funding_rate_8h == 0.0
    assert decoded.long_short_account_ratio is None


def test_a_known_empty_schedule_differs_from_an_untracked_token() -> None:
    # The distinction FundamentalsEvent's docstring exists to protect:
    # "we looked, nothing is coming" must not decode the same as "DefiLlama
    # does not track this token". Both carry next_unlock_at=None.
    known = parse_event(
        FundamentalsEvent(
            source=Source.DEFILLAMA,
            symbol="AAVE",
            coin_id="aave",
            has_unlock_schedule=True,
        ).as_kafka_value()
    )
    untracked = parse_event(
        FundamentalsEvent(
            source=Source.DEFILLAMA,
            symbol="XYZ",
            coin_id="xyz",
            has_unlock_schedule=False,
        ).as_kafka_value()
    )
    assert known.next_unlock_at is None and untracked.next_unlock_at is None
    assert known.has_unlock_schedule != untracked.has_unlock_schedule
