"""SentimentDbWorker: score unscored rows, aggregate, publish SentimentEvent."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cmi_common.sources import FakeContentRepository, RawItem

_spec = importlib.util.spec_from_file_location(
    "sworker",
    Path(__file__).resolve().parents[1]
    / "services"
    / "sentiment-service"
    / "app"
    / "worker.py",
)
sw = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = sw
_spec.loader.exec_module(sw)


class FakeScorer:
    def score(self, text: str):
        from types import SimpleNamespace

        # positive if it mentions "up", else neutral
        val = 0.8 if "up" in text else 0.0
        return SimpleNamespace(score=val, confidence=0.9, model_name="fake")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, _topic, event) -> None:
        self.published.append(event)


async def test_scores_marks_and_publishes_per_symbol() -> None:
    repo = FakeContentRepository()
    await repo.insert_items(
        [
            RawItem(
                source="bluesky",
                kind="social",
                external_id="1",
                text="$BTC up",
                symbols=["BTC"],
                engagement=5.0,
            ),
            RawItem(
                source="rss",
                kind="news",
                external_id="2",
                title="ETH",
                text="steady",
                symbols=["ETH"],
            ),
        ]
    )
    producer = FakeProducer()
    worker = sw.SentimentDbWorker(repo, FakeScorer(), producer, batch=10)

    processed = await worker.run_once()

    assert processed == 2
    assert len(await repo.fetch_unscored(10)) == 0  # all marked scored
    symbols = {e.symbol for e in producer.published}
    assert symbols == {"BTC", "ETH"}  # one SentimentEvent per symbol
    assert repo.aggregates  # aggregate rows upserted


async def test_symbolless_item_scored_as_market() -> None:
    repo = FakeContentRepository()
    await repo.insert_items(
        [
            RawItem(source="gdelt", kind="news", external_id="9", title="t", text="up"),
        ]
    )
    producer = FakeProducer()
    worker = sw.SentimentDbWorker(repo, FakeScorer(), producer, batch=10)

    await worker.run_once()

    assert {e.symbol for e in producer.published} == {"MARKET"}
