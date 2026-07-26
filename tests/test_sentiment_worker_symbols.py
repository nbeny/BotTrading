"""The worker no longer invents symbols: normalization guarantees them upstream."""

from __future__ import annotations

from datetime import UTC, datetime

from service_modules import load_service_module

SentimentDbWorker = load_service_module("sentiment-service", "worker").SentimentDbWorker


class Row:
    """Stands in for a raw_content row as fetch_unscored returns it."""

    def __init__(self, symbols: list[str]) -> None:
        self.id = 1
        self.kind = "news"
        self.source = "rss"
        self.title = "SEC approves a new framework"
        self.text = ""
        self.symbols = symbols
        self.engagement = 0.0
        self.published_at = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


class FakeRepo:
    def __init__(self, rows: list[Row]) -> None:
        self._rows = rows
        self.aggregated: list[str] = []

    async def fetch_unscored(self, batch: int) -> list[Row]:
        rows, self._rows = self._rows, []
        return rows

    async def mark_scored(self, row_id: int, **kw: object) -> None:
        return None

    async def upsert_aggregate(self, *, symbol: str, **kw: object) -> None:
        self.aggregated.append(symbol)


class FakeScorer:
    class _R:
        score = 0.5
        confidence = 0.9
        model_name = "fake"

    def score(self, text: str) -> _R:
        return self._R()


class FakeProducer:
    async def publish(self, topic: object, event: object) -> None:
        return None


async def test_market_is_taken_from_the_row_not_invented() -> None:
    # Gate 3 assigns MARKET at collection time. A fallback here would silently
    # re-admit rows the relevance gate was supposed to have rejected.
    repo = FakeRepo([Row(symbols=["MARKET"])])
    worker = SentimentDbWorker(repo, FakeScorer(), FakeProducer())
    await worker.run_once()
    assert repo.aggregated == ["MARKET"]


async def test_a_symbolless_row_aggregates_nothing() -> None:
    # If such a row ever reaches the worker the invariant is broken upstream;
    # the worker must not paper over it by inventing MARKET.
    repo = FakeRepo([Row(symbols=[])])
    worker = SentimentDbWorker(repo, FakeScorer(), FakeProducer())
    await worker.run_once()
    assert repo.aggregated == []
