"""Market-wide sentiment reaches decisions instead of being discarded.

Crypto content that names no coin -- regulation, macro, exchange incidents -- is
stored under the pseudo-symbol MARKET. It was 23% of all sentiment in production
and nothing consumed it: the decision engine keys on real symbols and dropped
the MARKET events on the floor.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent

de = load_service_module("decision-engine", "engine")
fm = load_service_module("decision-engine", "features_map")

_spec = importlib.util.spec_from_file_location(
    "de_scoring_regime",
    Path(__file__).resolve().parents[1]
    / "services"
    / "decision-engine"
    / "app"
    / "scoring.py",
)
scoring = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scoring  # required for dataclass(slots=True)
_spec.loader.exec_module(scoring)


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic, event) -> None:
        self.published.append((topic, event))


# --- scoring -----------------------------------------------------------------


def test_a_symbols_own_sentiment_always_wins_over_the_regime() -> None:
    own = scoring.score(
        scoring.Features(
            price_change_pct_24h=0.0, sentiment_score=0.8, market_sentiment=-0.9
        )
    )
    without = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, sentiment_score=0.8)
    )
    assert own.breakdown["news_score"] == without.breakdown["news_score"]


def test_the_regime_moves_the_score_in_the_direction_of_the_mood() -> None:
    # Baseline is a *neutral* regime, not Features(): with no signal at all
    # _norm_news short-circuits to 0.0, which is also what maximally bearish
    # scores. That conflation predates this change and is left alone here.
    bullish = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, market_sentiment=0.8)
    )
    flat = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, market_sentiment=0.0)
    )
    bearish = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, market_sentiment=-0.8)
    )
    assert (
        bearish.breakdown["news_score"]
        < flat.breakdown["news_score"]
        < bullish.breakdown["news_score"]
    )


def test_the_regime_is_damped_relative_to_a_symbols_own_reading() -> None:
    # Same number, but market-wide: it must move the score less than a direct
    # read would, because it is not about this symbol.
    direct = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, sentiment_score=0.8)
    )
    regime = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, market_sentiment=0.8)
    )
    # The baseline is a *measured* neutral, not an empty symbol: since
    # renormalisation, knowing nothing excludes the news axis entirely rather
    # than valuing it at the midpoint, so an empty Features has no news_score
    # to subtract from.
    neutral = scoring.score(
        scoring.Features(price_change_pct_24h=0.0, sentiment_score=0.0)
    )
    direct_lift = direct.breakdown["news_score"] - neutral.breakdown["news_score"]
    regime_lift = regime.breakdown["news_score"] - neutral.breakdown["news_score"]
    assert 0 < regime_lift < direct_lift


def test_the_regime_does_not_inflate_confidence() -> None:
    # Confidence measures symbol-specific evidence. A market read is identical
    # for every symbol, so counting it would lift the whole book at once.
    # Both carry the same symbol-specific evidence -- enough of it to clear the
    # minimum-evidence floor on their own -- so the only difference is the
    # regime read.
    anchor = dict(price_change_pct_24h=0.0, volume_spike_ratio=2.0)
    with_regime = scoring.score(scoring.Features(**anchor, market_sentiment=0.9))
    without = scoring.score(scoring.Features(**anchor))
    assert with_regime.confidence == without.confidence
    # And it does reach the score — it is confidence it must not touch.
    assert "news_score" in with_regime.breakdown


def test_weights_still_sum_to_one() -> None:
    # The regime rides inside news_score precisely so the tuned model is untouched.
    assert abs(sum(scoring.WEIGHTS.values()) - 1.0) < 1e-9


# --- engine ------------------------------------------------------------------
#
# The engine used to retain the regime read itself (TTL-gated in-memory state,
# fed by a SentimentEvent branch in handle()). That state is gone -- the
# engine is now a pure function of the AnalysisEvent it receives, and
# market_sentiment travels in event.meta["features"] like every other axis
# (see tests/test_decision_engine_stateless.py, tests/test_haiku_market_sentiment.py
# for where haiku stamps it in). What follows is the one end-to-end test that
# behavior still deserves, moved to that entry point.


def _analysis_with(features: dict) -> AnalysisEvent:
    # price_change_pct_24h/volume_spike_ratio must also ride in the features
    # dict: the engine reads features_from(event.meta["features"]) alone, and
    # never these top-level AnalysisEvent fields (those mirror the dict only
    # in production, at the one site ai-worker-haiku builds the event).
    # Without this, market_trend/volume_growth would never clear
    # _MIN_PRESENT_WEIGHT on their own, and both engines below would reject at
    # score 0 regardless of the axis under test.
    return AnalysisEvent(
        symbol="SOL",
        opportunity_score=50,
        confidence=0.5,
        reason="r",
        price_change_pct_24h=5.0,
        volume_spike_ratio=3.0,
        meta={
            "features": {
                "price_change_pct_24h": 5.0,
                "volume_spike_ratio": 3.0,
                **features,
            }
        },
    )


async def test_the_regime_changes_the_score_of_an_analysed_symbol() -> None:
    """End to end: a market-wide reading alters an analysis with no sentiment
    of its own."""
    plain = de.DecisionEngine(FakeProducer(), decision_threshold=101)
    await plain.handle(_analysis_with({}))

    biased = de.DecisionEngine(FakeProducer(), decision_threshold=101)
    await biased.handle(_analysis_with({"market_sentiment": -0.9}))

    plain_score = plain._producer.published[0][1].reason
    biased_score = biased._producer.published[0][1].reason
    # Both are below-threshold rejections whose reason quotes the score.
    assert plain_score != biased_score


# --- liquidity proxy ---------------------------------------------------------
#
# _liquidity now lives only in features_map.py (engine.py builds Features via
# features_from, which calls it) -- see tests/test_features_from_replay.py for
# the mapping-level coverage this mirrors through the engine end to end.


def test_dex_liquidity_is_used_when_present() -> None:
    assert fm._liquidity({"liquidity_usd": 2_000_000.0}) == 2_000_000.0


def test_volume_stands_in_when_there_is_no_dex_reading() -> None:
    # CEX-listed pairs never produce a DexEvent, so reading liquidity_usd alone
    # left liquidity_score at zero for essentially the whole flow: it was
    # populated in 0 of 12,183 production signals, killing 15% of the weight.
    assert fm._liquidity({"volume_24h_usd": 9_203_643.0}) == 9_203_643.0


def test_a_zero_dex_reading_falls_through_to_the_proxy() -> None:
    # A dex pair with no liquidity arrives as 0.0, not None -- haiku's scorer
    # documents this exact shape. Zero is not a reading.
    assert (
        fm._liquidity({"liquidity_usd": 0.0, "volume_24h_usd": 500_000.0}) == 500_000.0
    )


def test_neither_reading_stays_none_rather_than_inventing_a_number() -> None:
    assert fm._liquidity({}) is None
    assert fm._liquidity({"liquidity_usd": 0.0, "volume_24h_usd": 0.0}) is None


async def test_the_proxy_reaches_the_score_through_the_engine() -> None:
    engine = de.DecisionEngine(FakeProducer(), decision_threshold=101)
    await engine.handle(_analysis_with({"volume_24h_usd": 9_203_643.0}))
    with_proxy = engine._producer.published[0][1].reason

    bare = de.DecisionEngine(FakeProducer(), decision_threshold=101)
    await bare.handle(_analysis_with({}))
    without = bare._producer.published[0][1].reason

    assert with_proxy != without
