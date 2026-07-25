"""HaikuWorker._extract derives social/news presence from SentimentEvent.input_kind."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events.sentiment import SentimentEvent

hw = load_service_module("ai-worker-haiku", "worker", "haiku_app")


def _extract(event):
    # _extract is a bound method needing self, but it only reads instance-free
    # branches here; call it on a bare instance with stubbed collaborators.
    worker = hw.HaikuWorker.__new__(hw.HaikuWorker)
    return worker._extract(event)


def test_news_sentiment_sets_has_news() -> None:
    ev = SentimentEvent(
        symbol="BTC",
        sentiment_score=0.6,
        confidence=0.8,
        model_name="m",
        input_kind="news",
        sample_size=1,
    )
    symbol, fields, _topic = _extract(ev)
    assert symbol == "BTC"
    assert fields["sentiment_score"] == 0.6
    assert fields["has_news"] is True
    assert "has_social" not in fields


def test_social_sentiment_sets_has_social() -> None:
    ev = SentimentEvent(
        symbol="ETH",
        sentiment_score=-0.2,
        confidence=0.5,
        model_name="m",
        input_kind="social",
        sample_size=1,
    )
    symbol, fields, _topic = _extract(ev)
    assert symbol == "ETH"
    assert fields["has_social"] is True
    assert "has_news" not in fields
