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
