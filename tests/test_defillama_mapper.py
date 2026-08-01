"""Protocol rows + fees + unlocks -> FundamentalsEvent."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from service_modules import load_service_module

from cmi_common.events import FundamentalsEvent

mapper = load_service_module("collector-defillama", "domain.mapper")
unlocks = load_service_module("collector-defillama", "domain.unlocks")

# coin_id -> symbol, as the tokens table supplies it.
KNOWN = {"aave": "AAVE", "ethereum": "ETH"}


def _protocol(
    slug: str, gecko: str | None, tvl: float, change_7d: float | None
) -> dict[str, Any]:
    return {"slug": slug, "gecko_id": gecko, "tvl": tvl, "change_7d": change_7d}


def test_protocol_maps_to_an_event_keyed_by_the_known_symbol() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 5_000_000.0, 3.5)], fees={}, unlocks={}, known=KNOWN
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, FundamentalsEvent)
    assert event.symbol == "AAVE"
    assert event.coin_id == "aave"
    assert event.tvl_usd == Decimal("5000000")
    assert event.tvl_change_pct_7d == 3.5


def test_protocol_without_a_gecko_id_is_dropped_not_guessed() -> None:
    # Matching on the ticker instead would reintroduce exactly the ambiguity
    # that collector-kraken's ambiguous_symbols exists to record.
    assert (
        mapper.to_fundamentals_events(
            [_protocol("mystery", None, 1.0, 0.0)], fees={}, unlocks={}, known=KNOWN
        )
        == []
    )


def test_protocol_we_do_not_track_is_dropped() -> None:
    assert (
        mapper.to_fundamentals_events(
            [_protocol("obscure", "obscure-coin", 1.0, 0.0)],
            fees={},
            unlocks={},
            known=KNOWN,
        )
        == []
    )


def test_parent_and_child_protocols_sum_into_one_event() -> None:
    # DefiLlama lists deployments separately; a token's TVL is the token's, not
    # one deployment's, so summing before emission is what makes the number mean
    # what the axis assumes it means.
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 3_000_000.0, 2.0),
            _protocol("aave-v3", "aave", 2_000_000.0, 6.0),
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    assert len(events) == 1
    assert events[0].tvl_usd == Decimal("5000000")


def test_tvl_weighted_change_is_used_when_deployments_disagree() -> None:
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 3_000_000.0, 0.0),
            _protocol("aave-v3", "aave", 1_000_000.0, 8.0),
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    # (3M*0 + 1M*8) / 4M == 2.0
    assert events[0].tvl_change_pct_7d == 2.0


def test_fees_are_joined_by_slug_not_by_gecko_id() -> None:
    # Verified against the live API: /overview/fees carries no gecko_id at all
    # (0 of 2514 rows), so the join has to go through slug. Keying on gecko_id
    # would find nothing and report every protocol as fee-less.
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={
            "aave-v3": {"total24h": 42_000.0, "total7d": 200.0, "total14dto7d": 160.0}
        },
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("42000")
    # 7d-over-7d is derived: there is no change_7dover7d field in the payload.
    assert events[0].fees_change_pct_7d == 25.0


def test_fees_are_summed_across_a_tokens_deployments() -> None:
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 1.0, 0.0),
            _protocol("aave-v3", "aave", 1.0, 0.0),
        ],
        fees={
            "aave-v2": {"total24h": 1_000.0, "total7d": 50.0, "total14dto7d": 100.0},
            "aave-v3": {"total24h": 3_000.0, "total7d": 150.0, "total14dto7d": 100.0},
        },
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("4000")
    # (200 - 200) / 200 -> 0.0, computed on the sums rather than averaged per
    # deployment, which would have given +25%.
    assert events[0].fees_change_pct_7d == 0.0


def test_a_zero_fee_baseline_yields_no_change_rather_than_a_division() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={"aave-v3": {"total24h": 10.0, "total7d": 50.0, "total14dto7d": 0.0}},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_change_pct_7d is None


def test_a_protocol_with_no_fee_row_reports_no_fees() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)], fees={}, unlocks={}, known=KNOWN
    )
    assert events[0].fees_24h_usd is None
    assert events[0].fees_change_pct_7d is None


def test_a_measured_zero_tvl_is_a_number_not_an_absence() -> None:
    # A protocol that reports zero TVL has been measured. Reporting None would
    # make it indistinguishable from one DefiLlama does not cover, and the
    # scoring model excludes an absent axis rather than penalising it -- so the
    # dead protocol would score better than a live one.
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 0.0, 0.0)], fees={}, unlocks={}, known=KNOWN
    )
    assert events[0].tvl_usd == Decimal("0")


def test_a_measured_zero_fee_day_is_a_number_not_an_absence() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={"aave-v3": {"total24h": 0.0, "total7d": 0.0, "total14dto7d": 100.0}},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("0")
    # And the 7d change is a real -100%, not an absence.
    assert events[0].fees_change_pct_7d == -100.0


def test_a_protocol_row_without_a_tvl_key_reports_unknown_not_zero() -> None:
    events = mapper.to_fundamentals_events(
        [{"slug": "aave-v3", "gecko_id": "aave"}], fees={}, unlocks={}, known=KNOWN
    )
    assert events[0].tvl_usd is None


def test_a_protocol_with_no_published_change_reports_unknown_not_flat() -> None:
    # 906 of 2325 gecko-bearing live rows have change_7d: null, 127 of them
    # with a positive TVL. On a momentum axis a fabricated 0.0 is not a neutral
    # default -- it asserts that nothing moved.
    events = mapper.to_fundamentals_events(
        [{"slug": "aave-v3", "gecko_id": "aave", "tvl": 5_000_000.0}],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].tvl_usd == Decimal("5000000")
    assert events[0].tvl_change_pct_7d is None


def test_a_null_change_is_treated_the_same_as_an_absent_one() -> None:
    events = mapper.to_fundamentals_events(
        [{"slug": "aave-v3", "gecko_id": "aave", "tvl": 5e6, "change_7d": None}],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].tvl_change_pct_7d is None


def test_the_change_is_weighted_only_over_deployments_that_reported_one() -> None:
    # A silent 3M deployment must not dilute a measured +8% on 1M down to 2%.
    events = mapper.to_fundamentals_events(
        [
            {"slug": "aave-v2", "gecko_id": "aave", "tvl": 3_000_000.0},
            {
                "slug": "aave-v3",
                "gecko_id": "aave",
                "tvl": 1_000_000.0,
                "change_7d": 8.0,
            },
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].tvl_change_pct_7d == 8.0
    assert events[0].tvl_usd == Decimal("4000000")


def test_null_fee_totals_report_unknown_not_zero() -> None:
    # 300 of 2514 live fee rows have total24h: null, 274 are all-null.
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={"aave-v3": {"total24h": None, "total7d": None, "total14dto7d": None}},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd is None
    assert events[0].fees_change_pct_7d is None


def test_fee_sums_stay_exact() -> None:
    # 517 of 2514 live rows carry a fractional total24h; summing in float and
    # converting at the end writes 17-digit artifacts into the event.
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 1.0, 0.0),
            _protocol("aave-v3", "aave", 1.0, 0.0),
        ],
        fees={
            "aave-v2": {"total24h": 0.1},
            "aave-v3": {"total24h": 0.2},
        },
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("0.3")


def test_a_fee_row_matching_no_protocol_is_ignored() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={"orphan": {"total24h": 999.0}},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd is None


def test_two_tokens_accumulate_independently() -> None:
    # Every existing case produces exactly one bucket, so per-token isolation
    # of the accumulator is never exercised.
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v3", "aave", 5_000_000.0, 3.0),
            _protocol("uni", "ethereum", 1_000_000.0, -2.0),
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    by_symbol = {e.symbol: e for e in events}
    assert by_symbol["AAVE"].tvl_usd == Decimal("5000000")
    assert by_symbol["ETH"].tvl_usd == Decimal("1000000")
    assert by_symbol["ETH"].tvl_change_pct_7d == -2.0


def test_a_known_schedule_with_a_pending_unlock_is_carried() -> None:
    due = datetime(2026, 8, 10, tzinfo=UTC)
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 1.0, 0.0)],
        fees={},
        unlocks={"aave": unlocks.Unlock(at=due, pct_supply=2.5)},
        known=KNOWN,
    )
    assert events[0].has_unlock_schedule is True
    assert events[0].next_unlock_at == due
    assert events[0].next_unlock_pct_supply == 2.5


def test_a_known_schedule_with_nothing_pending_is_a_measurement() -> None:
    # None under a True flag is the good news: we looked, and nothing is coming.
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 1.0, 0.0)],
        fees={},
        unlocks={"aave": None},
        known=KNOWN,
    )
    assert events[0].has_unlock_schedule is True
    assert events[0].next_unlock_at is None
    assert events[0].next_unlock_pct_supply is None


def test_an_untracked_token_reports_no_schedule() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 1.0, 0.0)], fees={}, unlocks={}, known=KNOWN
    )
    assert events[0].has_unlock_schedule is False


def test_emission_key_uses_the_parent_slug_when_there_is_one() -> None:
    # Measured against the live API: the emissions list contains "aave" while
    # /protocols only ever contains "aave-v2", "aave-v3" and friends. Matching
    # on slug alone covers 220 of 359 scheduled protocols and misses Aave
    # outright; going through parentProtocol covers 335.
    assert (
        mapper.emission_key({"slug": "aave-v3", "parentProtocol": "parent#aave"})
        == "aave"
    )


def test_emission_key_falls_back_to_the_slug() -> None:
    assert mapper.emission_key({"slug": "drift"}) == "drift"


def test_emission_key_is_absent_when_the_row_has_neither() -> None:
    assert mapper.emission_key({"name": "nameless"}) is None


def test_negative_fees_are_carried_rather_than_aborting_the_cycle() -> None:
    # Fees are a net flow: 8 of 2514 live rows carry a negative total24h
    # (azuro -$14,431, fusion-by-ipor -$15,325). A ge=0 bound raised
    # ValidationError inside the collector's emit loop, which has no per-event
    # guard, so one such protocol in our universe published nothing for ANY
    # token that cycle.
    events = mapper.to_fundamentals_events(
        [_protocol("azuro", "aave", 1.0, 0.0)],
        fees={
            "azuro": {"total24h": -14_431.0, "total7d": -100.0, "total14dto7d": 50.0}
        },
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("-14431.0")


def test_a_negative_tvl_row_is_skipped_without_losing_the_other_tokens() -> None:
    # Unlike fees, TVL is a stock: negative is nonsense data, not a
    # measurement. pinjam-labs reports tvl -1654.77 on the live API, and
    # tvl_usd keeps its ge=0 bound, so the row is dropped rather than summed.
    events = mapper.to_fundamentals_events(
        [
            {"slug": "pinjam-labs", "gecko_id": "ethereum", "tvl": -1654.77},
            _protocol("aave-v2", "aave", 5_000_000.0, 2.0),
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    by_symbol = {e.symbol: e for e in events}
    assert by_symbol["AAVE"].tvl_usd == Decimal("5000000")
    # Skipped, so unmeasured — not a fabricated zero.
    assert by_symbol["ETH"].tvl_usd is None
