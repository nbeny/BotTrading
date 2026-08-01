"""Normalisation of the positioning and fundamentals axes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "de_scoring_axes",
    Path(__file__).resolve().parents[1]
    / "services"
    / "decision-engine"
    / "app"
    / "scoring.py",
)
scoring = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scoring
_spec.loader.exec_module(scoring)


# --- positioning -----------------------------------------------------------


def test_positive_funding_lowers_positioning() -> None:
    # THE sign test. Positive funding means longs are paying shorts, i.e. the
    # crowded side. Getting this backwards yields a model that is confidently
    # wrong rather than obviously broken, so it is asserted explicitly.
    crowded = scoring._norm_positioning(funding=0.001, ratio=None, oi_change=None)
    neutral = scoring._norm_positioning(funding=0.0, ratio=None, oi_change=None)
    squeezed = scoring._norm_positioning(funding=-0.001, ratio=None, oi_change=None)
    assert crowded < neutral < squeezed
    assert abs(neutral - 0.5) < 1e-9


def test_the_funding_scale_discriminates_across_the_real_distribution() -> None:
    # Measured across all 854 Binance perps on 2026-07-31: p05 -0.000156,
    # p95 +0.000159. An earlier draft divided by 0.0004 and spanned only 0.19
    # between those percentiles -- an axis that moves a fifth of its range over
    # 90% of the book is decoration, not signal.
    p05 = scoring._norm_positioning(funding=-0.000156, ratio=None, oi_change=None)
    p95 = scoring._norm_positioning(funding=0.000159, ratio=None, oi_change=None)
    assert p05 - p95 > 0.5


def test_a_crowded_long_ratio_lowers_positioning() -> None:
    crowded = scoring._norm_positioning(funding=None, ratio=2.0, oi_change=None)
    balanced = scoring._norm_positioning(funding=None, ratio=1.0, oi_change=None)
    assert crowded < balanced
    assert abs(balanced - 0.5) < 1e-9


def test_rising_open_interest_raises_positioning() -> None:
    rising = scoring._norm_positioning(funding=None, ratio=None, oi_change=20.0)
    flat = scoring._norm_positioning(funding=None, ratio=None, oi_change=0.0)
    assert rising > flat


def test_positioning_is_the_mean_of_present_terms() -> None:
    # Funding alone (broad tier) must give a usable reading; a missing ratio is
    # not a zero term dragging the axis down.
    funding_only = scoring._norm_positioning(funding=-0.001, ratio=None, oi_change=None)
    assert funding_only > 0.9


def test_a_measured_zero_oi_change_is_not_an_absence() -> None:
    flat = scoring._norm_positioning(funding=None, ratio=None, oi_change=0.0)
    assert flat is not None
    assert abs(flat - 0.5) < 1e-9


def test_positioning_absent_when_nothing_is_known() -> None:
    assert scoring._norm_positioning(funding=None, ratio=None, oi_change=None) is None


def test_a_nonsensical_ratio_is_ignored_rather_than_logged_of() -> None:
    # A zero or negative account ratio cannot happen and log(0) would raise.
    # The event schema rejects it at construction; this is the second gate.
    assert scoring._norm_positioning(funding=None, ratio=0.0, oi_change=None) is None


# --- fundamentals ----------------------------------------------------------


def _fund(**kw):
    base = dict(
        tvl_change=None,
        fees_change=None,
        unlock_pct=None,
        unlock_days=None,
        has_schedule=False,
    )
    base.update(kw)
    return scoring._norm_fundamentals(**base)


def test_rising_tvl_raises_fundamentals() -> None:
    assert _fund(tvl_change=15.0) > _fund(tvl_change=0.0)


def test_an_imminent_large_unlock_crushes_fundamentals() -> None:
    imminent = _fund(unlock_pct=5.0, unlock_days=3.0, has_schedule=True)
    assert imminent is not None
    assert imminent < 0.15


def test_a_distant_unlock_is_harmless() -> None:
    assert _fund(unlock_pct=5.0, unlock_days=30.0, has_schedule=True) == 1.0


def test_a_known_empty_schedule_is_the_best_reading() -> None:
    assert _fund(has_schedule=True) == 1.0


def test_an_untracked_token_gets_no_unlock_term() -> None:
    # Absent from DefiLlama's schedule list is not a clean bill of health; with
    # no other input the axis must be absent, not 1.0.
    assert _fund(has_schedule=False) is None


def test_unlock_severity_scales_with_size() -> None:
    small = _fund(unlock_pct=1.0, unlock_days=0.0, has_schedule=True)
    large = _fund(unlock_pct=5.0, unlock_days=0.0, has_schedule=True)
    assert large < small < 1.0


def test_unlock_severity_saturates_rather_than_going_negative() -> None:
    # A 40% unlock is not eight times worse than a 5% one; the term floors at 0.
    assert _fund(unlock_pct=40.0, unlock_days=0.0, has_schedule=True) == 0.0


def test_a_measured_zero_tvl_change_is_not_an_absence() -> None:
    flat = _fund(tvl_change=0.0)
    assert flat is not None
    assert abs(flat - 0.5) < 1e-9
