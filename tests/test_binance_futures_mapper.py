"""Perp ticker resolution and DerivativesEvent construction."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

from cmi_common.events import DerivativesEvent

symbols = load_service_module("collector-binance-futures", "domain.symbols")
mapper = load_service_module("collector-binance-futures", "domain.mapper")


def test_usdt_perp_resolves_to_its_base_symbol() -> None:
    assert symbols.base_symbol("BTCUSDT") == "BTC"
    assert symbols.base_symbol("1000PEPEUSDT") == "1000PEPE"


def test_non_usdt_quotes_are_not_resolved() -> None:
    # Coin-margined and BUSD pairs price the same asset on a different unit;
    # mixing them into one funding reading would compare unlike things.
    assert symbols.base_symbol("BTCUSD_PERP") is None
    assert symbols.base_symbol("BTCBUSD") is None


def test_a_bare_quote_with_no_base_is_not_a_symbol() -> None:
    assert symbols.base_symbol("USDT") is None


def test_resolution_requires_the_symbol_to_be_one_we_price() -> None:
    assert symbols.resolve("BTCUSDT", priced={"BTC"}, ambiguous=set()) == "BTC"
    assert symbols.resolve("XYZUSDT", priced={"BTC"}, ambiguous=set()) is None


def test_an_ambiguous_ticker_is_skipped_not_arbitrated() -> None:
    # A ticker claimed by two coin ids cannot be attributed to either; recording
    # the ambiguity beats silently picking the higher-ranked one.
    assert symbols.resolve("SOLUSDT", priced={"SOL"}, ambiguous={"SOL"}) is None


def test_funding_row_becomes_an_event_with_the_annualised_rate() -> None:
    events = mapper.to_derivatives_events(
        [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
        priced={"BTC"},
        ambiguous=set(),
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, DerivativesEvent)
    assert event.symbol == "BTC"
    assert event.funding_rate_8h == 0.0001
    # Three funding periods a day, 365 days: 0.0001 * 3 * 365 * 100 == 10.95%
    assert event.funding_annualized_pct == 10.95


def test_a_measured_zero_funding_rate_still_produces_an_event() -> None:
    # Zero funding is a reading — perfectly balanced longs and shorts — and
    # must not be dropped as if we had failed to fetch it.
    events = mapper.to_derivatives_events(
        [{"symbol": "BTCUSDT", "lastFundingRate": "0"}],
        priced={"BTC"},
        ambiguous=set(),
    )
    assert len(events) == 1
    assert events[0].funding_rate_8h == 0.0


def test_a_negative_funding_rate_is_carried_through() -> None:
    # Negative funding means shorts pay longs — the contrarian-bullish case the
    # positioning axis exists to catch.
    events = mapper.to_derivatives_events(
        [{"symbol": "BTCUSDT", "lastFundingRate": "-0.0002"}],
        priced={"BTC"},
        ambiguous=set(),
    )
    assert events[0].funding_rate_8h == -0.0002
    assert events[0].funding_annualized_pct == -21.9


def test_rows_we_cannot_resolve_are_dropped() -> None:
    assert (
        mapper.to_derivatives_events(
            [{"symbol": "GHOSTUSDT", "lastFundingRate": "0.0001"}],
            priced={"BTC"},
            ambiguous=set(),
        )
        == []
    )


def test_a_missing_funding_rate_yields_no_event_rather_than_a_zero() -> None:
    assert (
        mapper.to_derivatives_events(
            [{"symbol": "BTCUSDT"}], priced={"BTC"}, ambiguous=set()
        )
        == []
    )


def test_a_null_funding_rate_also_yields_no_event() -> None:
    assert (
        mapper.to_derivatives_events(
            [{"symbol": "BTCUSDT", "lastFundingRate": None}],
            priced={"BTC"},
            ambiguous=set(),
        )
        == []
    )


def test_majors_detail_is_merged_onto_the_funding_event() -> None:
    event = mapper.with_majors_detail(
        mapper.to_derivatives_events(
            [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
            priced={"BTC"},
            ambiguous=set(),
        )[0],
        open_interest_usd=Decimal("1000000"),
        oi_change_pct_24h=5.0,
        long_short_ratio=1.8,
    )
    assert event.open_interest_usd == Decimal("1000000")
    assert event.open_interest_change_pct_24h == 5.0
    assert event.long_short_account_ratio == 1.8
    assert event.funding_rate_8h == 0.0001  # the broad-tier reading survives


def test_merging_absent_detail_leaves_the_fields_unmeasured() -> None:
    # A majors symbol whose per-symbol calls failed is not a degraded reading;
    # those fields were simply never measured, and must stay None.
    base = mapper.to_derivatives_events(
        [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
        priced={"BTC"},
        ambiguous=set(),
    )[0]
    event = mapper.with_majors_detail(
        base, open_interest_usd=None, oi_change_pct_24h=None, long_short_ratio=None
    )
    assert event.open_interest_usd is None
    assert event.long_short_account_ratio is None
    assert event.funding_rate_8h == 0.0001


def test_merging_returns_a_copy_and_does_not_mutate_the_original() -> None:
    # DerivativesEvent is frozen; this pins that the merge respects it.
    base = mapper.to_derivatives_events(
        [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
        priced={"BTC"},
        ambiguous=set(),
    )[0]
    merged = mapper.with_majors_detail(
        base,
        open_interest_usd=Decimal("5"),
        oi_change_pct_24h=1.0,
        long_short_ratio=1.1,
    )
    assert base.open_interest_usd is None
    assert merged is not base
