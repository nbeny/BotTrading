"""Truth table for the Haiku triage scorer's diagnostic outputs.

The scorer decides whether a symbol reaches the senior (Sonnet) analyst. Before
this test existed, a signal could be dropped for three different reasons and the
pipeline reported none of them. Each case below pins one reason.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1] / "services" / "ai-worker-haiku"
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

_spec = importlib.util.spec_from_file_location(
    "app.scorer", _APP_ROOT / "app" / "scorer.py"
)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = sc
_spec.loader.exec_module(sc)


def test_typical_major_pair_is_blocked_by_score() -> None:
    """The real-world case: BTC +3%, mild sentiment, no volume spike.

    Scores ~22 against an escalation floor of 60. This is why the pipeline
    produced no decisions at all.
    """
    r = sc.local_opportunity(
        {"price_change_pct_24h": 3.0, "sentiment_score": 0.3}
    )
    assert r.opportunity_score < 60
    assert r.escalate is False
    assert r.block_reason == "score_below_threshold"


def test_strong_score_but_calm_move_is_blocked_by_gate() -> None:
    """Score clears 60 but the setup is unanimous and liquid: no LLM needed."""
    r = sc.local_opportunity(
        {
            "price_change_pct_24h": 8.0,
            "volume_spike_ratio": 2.0,
            "sentiment_score": 0.9,
            "liquidity_usd": 900_000.0,
        }
    )
    assert r.opportunity_score >= 60
    assert r.ambiguous is False
    assert r.escalate is False
    assert r.block_reason == "gate_not_met"


def test_escalated_signal_reports_escalated() -> None:
    """Strong and high-momentum: crosses both the score and the gate."""
    r = sc.local_opportunity(
        {
            "price_change_pct_24h": 14.0,
            "volume_spike_ratio": 4.0,
            "sentiment_score": 0.9,
            "liquidity_usd": 900_000.0,
        }
    )
    assert r.escalate is True
    assert r.block_reason == "escalated"


def test_factors_present_counts_only_supplied_factors() -> None:
    r = sc.local_opportunity(
        {"price_change_pct_24h": 3.0, "sentiment_score": 0.3}
    )
    assert r.factors_present == 2

    full = sc.local_opportunity(
        {
            "price_change_pct_24h": 3.0,
            "volume_spike_ratio": 1.5,
            "sentiment_score": 0.3,
            "liquidity_usd": 500_000.0,
        }
    )
    assert full.factors_present == 4


def test_liquidity_source_is_unknown_when_absent() -> None:
    r = sc.local_opportunity({"price_change_pct_24h": 3.0, "sentiment_score": 0.3})
    assert r.liquidity_source == "unknown"


def test_liquidity_source_is_dex_when_supplied() -> None:
    r = sc.local_opportunity(
        {"price_change_pct_24h": 3.0, "sentiment_score": 0.3, "liquidity_usd": 500_000.0}
    )
    assert r.liquidity_source == "dex"


def test_ambiguity_no_longer_drags_confidence_under_the_risk_floor() -> None:
    """Regression guard for the contradiction found during design.

    The scorer escalates *ambiguous* setups to Sonnet, but the old confidence
    formula (0.3 + 0.4*liq + 0.3 if not ambiguous) gave exactly 0.50 to an
    ambiguous signal with unknown liquidity — below the risk engine's 0.55
    floor. Ambiguity is a reason to escalate, not a reason to distrust the data.
    """
    r = sc.local_opportunity(
        {"price_change_pct_24h": 5.0, "sentiment_score": -0.5}
    )
    assert r.ambiguous is True
    assert r.confidence >= 0.55
