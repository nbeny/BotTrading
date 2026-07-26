"""Level-1 sentiment scoring: HuggingFace Transformers with a lexicon fallback.

The heavy model (CryptoBERT / FinBERT / RoBERTa) is loaded lazily so the
service stays importable and testable without downloading weights. When
transformers is unavailable (e.g. unit tests), a fast lexical heuristic keeps
the pipeline flowing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_POSITIVE = {
    "bull", "bullish", "moon", "pump", "surge", "rally", "breakout",
    "gain", "up", "buy", "long", "adoption", "partnership", "listing",
}
_NEGATIVE = {
    "bear", "bearish", "dump", "crash", "rug", "scam", "hack", "exploit",
    "down", "sell", "short", "fud", "lawsuit", "ban", "delist",
}


@dataclass(slots=True)
class SentimentResult:
    score: float  # normalized to [-1, 1]
    confidence: float  # [0, 1]
    model_name: str


class SentimentScorer:
    """Wraps a HuggingFace text-classification pipeline with a fallback."""

    def __init__(self, model_name: str = "ElKulako/cryptobert") -> None:
        self._model_name = model_name
        self._pipeline = None
        self._loaded = False

    def _lazy_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            from transformers import pipeline  # type: ignore

            self._pipeline = pipeline(
                "text-classification",
                model=self._model_name,
                truncation=True,
                max_length=256,
                top_k=None,
            )
            logger.info("loaded HF sentiment model %s", self._model_name)
        except Exception as exc:
            logger.warning(
                "HF model unavailable (%s); using lexicon fallback", exc
            )
            self._pipeline = None

    def score(self, text: str) -> SentimentResult:
        self._lazy_load()
        if not text.strip():
            return SentimentResult(0.0, 0.0, "empty")
        if self._pipeline is not None:
            return self._score_hf(text)
        return self._score_lexicon(text)

    def _score_hf(self, text: str) -> SentimentResult:
        preds = self._pipeline(text)  # type: ignore[misc]
        # transformers returns list[list[dict]] with top_k=None.
        scores = preds[0] if isinstance(preds[0], list) else preds
        prob = {p["label"].lower(): float(p["score"]) for p in scores}

        def _get(*keys: str) -> float:
            return next((prob[k] for k in prob for key in keys if key in k), 0.0)

        p_bull = _get("pos", "bull")
        p_bear = _get("neg", "bear")
        p_neu = _get("neu")
        return SentimentResult(
            score=round(p_bull - p_bear, 4),
            confidence=round(1.0 - p_neu, 4),
            model_name=self._model_name,
        )

    def _score_lexicon(self, text: str) -> SentimentResult:
        tokens = [t.strip(".,!?$#").lower() for t in text.split()]
        pos = sum(t in _POSITIVE for t in tokens)
        neg = sum(t in _NEGATIVE for t in tokens)
        total = pos + neg
        if total == 0:
            return SentimentResult(0.0, 0.2, "lexicon")
        score = (pos - neg) / total
        confidence = min(1.0, total / 10)
        return SentimentResult(round(score, 4), round(confidence, 4), "lexicon")
