"""Sentiment scorer lexicon fallback (no model download in CI)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sent_scorer",
    Path(__file__).resolve().parents[1]
    / "services" / "sentiment-service" / "app" / "scorer.py",
)
scorer_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scorer_mod  # required for dataclass(slots=True)
_spec.loader.exec_module(scorer_mod)


def _lexicon_scorer():
    s = scorer_mod.SentimentScorer("does-not-exist/model")
    # Force the fallback path without attempting a heavy model download.
    s._loaded = True
    s._pipeline = None
    return s


def test_bullish_text_positive() -> None:
    r = _lexicon_scorer().score("bullish breakout, massive adoption and listing")
    assert r.score > 0
    assert r.model_name == "lexicon"


def test_bearish_text_negative() -> None:
    r = _lexicon_scorer().score("rug scam hack, everyone dump and sell")
    assert r.score < 0


def test_neutral_text_zero() -> None:
    r = _lexicon_scorer().score("the token exists on a blockchain")
    assert r.score == 0.0


def test_empty_text() -> None:
    r = _lexicon_scorer().score("   ")
    assert r.score == 0.0 and r.confidence == 0.0


class _FakePipe:
    def __init__(self, preds):
        self._preds = preds

    def __call__(self, text):
        return [self._preds]


def _hf_scorer(preds):
    s = scorer_mod.SentimentScorer("fake/model")
    s._loaded = True
    s._pipeline = _FakePipe(preds)
    return s


def test_continuous_score_bullish_lean() -> None:
    import pytest

    s = _hf_scorer(
        [{"label": "Bullish", "score": 0.7},
         {"label": "Neutral", "score": 0.2},
         {"label": "Bearish", "score": 0.1}]
    )
    r = s.score("btc to the moon")
    assert r.score == pytest.approx(0.6)       # 0.7 - 0.1
    assert r.confidence == pytest.approx(0.8)  # 1 - 0.2


def test_continuous_score_neutral_is_near_zero() -> None:
    import pytest

    s = _hf_scorer(
        [{"label": "Bullish", "score": 0.1},
         {"label": "Neutral", "score": 0.8},
         {"label": "Bearish", "score": 0.1}]
    )
    r = s.score("nothing happening")
    assert r.score == pytest.approx(0.0)
    assert r.confidence == pytest.approx(0.2)
