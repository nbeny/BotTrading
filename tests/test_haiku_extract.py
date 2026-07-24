"""HaikuWorker._extract derives social/news presence from SentimentEvent.input_kind."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cmi_common.events.sentiment import SentimentEvent

# worker.py uses ``from .features import FeatureStore``; add the service dir to
# sys.path and load worker.py as ``app.worker`` so the relative import resolves.
_APP_ROOT = Path(__file__).resolve().parents[1] / "services" / "ai-worker-haiku"
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

_spec = importlib.util.spec_from_file_location(
    "app.worker",
    _APP_ROOT / "app" / "worker.py",
)
hw = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = hw
_spec.loader.exec_module(hw)


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
