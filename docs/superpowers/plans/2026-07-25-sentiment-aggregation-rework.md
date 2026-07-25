# Sentiment Aggregation Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `content_sentiment_agg` into the read-plane source of truth: additive hourly buckets per (symbol, kind), arbitrary trailing windows (1h..5y) derived at read time, continuous HF sentiment score, and opt-in read-time exponential decay.

**Architecture:** The scoring worker writes one additive hourly bucket per scored item×symbol. A shared `SentimentAggReader` derives any trailing window by summing buckets and computing means once. The api-gateway exposes `/api/v1/sentiment/{windows,series,authors}` and its content-stats endpoint stops scanning `raw_content` for sentiment. Single (hourly) resolution — a `day` rollup tier is deliberately deferred.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, PostgreSQL 16 (timescale image, no `postgres-hll`), Alembic, FastAPI, pytest (`pytest.ini`/asyncio auto), HuggingFace transformers (CryptoBERT).

**Spec:** `docs/superpowers/specs/2026-07-25-sentiment-aggregation-rework-design.md`

---

## Reference facts (read before starting)

- Model: `libs/cmi_common/cmi_common/db/models.py:193-211` (`ContentSentimentAgg`).
- Repository + fake: `libs/cmi_common/cmi_common/sources/repository.py` — `SqlContentRepository.upsert_aggregate` (`:134-193`), `FakeContentRepository.upsert_aggregate` (`:252-289`), `ContentRepository` Protocol (`:64-76`), `_utcnow` (`:196-197`).
- Worker: `services/sentiment-service/app/worker.py` — `run_once` (`:56-98`), `_floor_hour` (`:101-105`), `WINDOW_SIZE = 3600` (`:29`).
- Scorer: `services/sentiment-service/app/scorer.py` — `_score_hf` (`:70-83`).
- Migration precedent: `migrations/alembic/versions/0003_raw_content.py` (creates both tables).
- api-gateway read: `services/api-gateway/app/read_api.py` — `compute_content_stats` sentiment block (`:185-226`); router `router = APIRouter(tags=["read"])` (`:32`); session dep `from .routers import get_session_dep` (`:30`); `_utcnow` (`:55-57`).
- api-gateway wiring: `services/api-gateway/app/main.py:38` binds `app.dependency_overrides[routers.get_session_dep] = db.session`; routers included at `:51-53`.
- Sources package exports: `libs/cmi_common/cmi_common/sources/__init__.py`.
- Existing repo tests: `tests/test_content_repository.py`.
- Frontend type: `frontend/src/lib/types/content.ts` (references `content_sentiment_agg`).

Run tests with: `python -m pytest <path> -v` from repo root. Migrations run inside the stack via `make migrate`; unit tests here do NOT need the DB except the reader integration test (marked/skipped if no DB — see Task 8).

---

## Task 1: Reshape the `ContentSentimentAgg` model

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py:193-211`

- [ ] **Step 1: Replace the model with the additive hourly-bucket schema**

Replace the whole `ContentSentimentAgg` class (`:193-211`) with:

```python
class ContentSentimentAgg(Base):
    """Per-symbol hourly rollup derived from scored raw_content.

    All stored quantities are additive so any trailing window is derived by
    summing the covering buckets; means are computed once at read time. The key
    is (symbol, kind, bucket_start) at a single hourly resolution.
    """

    __tablename__ = "content_sentiment_agg"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_sum: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    engagement_sum: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 2: Verify the module imports**

Run: `python -c "from cmi_common.db.models import ContentSentimentAgg; print(ContentSentimentAgg.__table__.primary_key.columns.keys())"`
Expected: `['symbol', 'kind', 'bucket_start']`

- [ ] **Step 3: Commit**

```bash
git add libs/cmi_common/cmi_common/db/models.py
git commit -m "refactor(db): additive hourly-bucket schema for content_sentiment_agg"
```

---

## Task 2: Additive `upsert_aggregate` in the repository + Protocol + Fake

**Files:**
- Modify: `libs/cmi_common/cmi_common/sources/repository.py` — Protocol (`:64-76`), `SqlContentRepository.upsert_aggregate` (`:134-193`), `FakeContentRepository` fields (`:205-207`) and `upsert_aggregate` (`:252-289`)
- Test: `tests/test_content_repository.py`

- [ ] **Step 1: Rewrite the failing fake-aggregate tests**

Replace the two aggregate tests (`test_fake_upsert_aggregate_accumulates` and `test_fake_upsert_aggregate_conflict_matches_sql_semantics`, `tests/test_content_repository.py:44-79`) with:

```python
async def test_fake_upsert_aggregate_creates_bucket() -> None:
    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await repo.upsert_aggregate(
        symbol="BTC", kind="social", bucket_start=ts,
        mentions=1, score_sum=0.5, confidence_sum=0.8,
        weighted_score_sum=0.4, engagement_sum=2.0,
    )
    agg = repo.aggregates[("BTC", "social", ts)]
    assert agg["mentions"] == 1
    assert agg["score_sum"] == 0.5
    assert agg["engagement_sum"] == 2.0


async def test_fake_upsert_aggregate_is_additive() -> None:
    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    kw = dict(symbol="BTC", kind="social", bucket_start=ts)
    await repo.upsert_aggregate(
        **kw, mentions=1, score_sum=0.5, confidence_sum=0.8,
        weighted_score_sum=0.4, engagement_sum=2.0,
    )
    await repo.upsert_aggregate(
        **kw, mentions=2, score_sum=1.8, confidence_sum=1.5,
        weighted_score_sum=1.6, engagement_sum=5.0,
    )
    agg = repo.aggregates[("BTC", "social", ts)]
    assert agg["mentions"] == 3          # 1 + 2
    assert agg["score_sum"] == 2.3       # 0.5 + 1.8
    assert agg["confidence_sum"] == 2.3  # 0.8 + 1.5
    assert agg["weighted_score_sum"] == 2.0  # 0.4 + 1.6
    assert agg["engagement_sum"] == 7.0  # 2.0 + 5.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_content_repository.py -v`
Expected: FAIL — `upsert_aggregate() got an unexpected keyword argument 'bucket_start'`

- [ ] **Step 3: Update the `ContentRepository` Protocol**

Replace `upsert_aggregate` in the Protocol (`repository.py:64-76`) with:

```python
    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        bucket_start: datetime,
        mentions: int,
        score_sum: float,
        confidence_sum: float,
        weighted_score_sum: float,
        engagement_sum: float,
    ) -> None: ...
```

- [ ] **Step 4: Rewrite `SqlContentRepository.upsert_aggregate`**

Replace the whole method (`repository.py:134-193`) with:

```python
    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        bucket_start: datetime,
        mentions: int,
        score_sum: float,
        confidence_sum: float,
        weighted_score_sum: float,
        engagement_sum: float,
    ) -> None:
        values = {
            "symbol": symbol,
            "kind": kind,
            "bucket_start": bucket_start,
            "mentions": mentions,
            "score_sum": score_sum,
            "confidence_sum": confidence_sum,
            "weighted_score_sum": weighted_score_sum,
            "engagement_sum": engagement_sum,
            "updated_at": _utcnow(),
        }
        stmt = pg_insert(ContentSentimentAgg).values(**values)
        # All columns are additive: on conflict, accumulate. Means (avg /
        # weighted_avg) are derived at read time from these sums, so there is no
        # running-mean drift here.
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "kind", "bucket_start"],
            set_={
                "mentions": ContentSentimentAgg.mentions + stmt.excluded.mentions,
                "score_sum": ContentSentimentAgg.score_sum + stmt.excluded.score_sum,
                "confidence_sum": (
                    ContentSentimentAgg.confidence_sum + stmt.excluded.confidence_sum
                ),
                "weighted_score_sum": (
                    ContentSentimentAgg.weighted_score_sum
                    + stmt.excluded.weighted_score_sum
                ),
                "engagement_sum": (
                    ContentSentimentAgg.engagement_sum + stmt.excluded.engagement_sum
                ),
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
```

- [ ] **Step 5: Rewrite `FakeContentRepository` aggregate state + method**

Change the `aggregates` field type annotation (`repository.py:205-207`) to:

```python
    aggregates: dict[tuple[str, str, datetime], dict[str, Any]] = field(
        default_factory=dict
    )
```

Replace `FakeContentRepository.upsert_aggregate` (`repository.py:252-289`) with:

```python
    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        bucket_start: datetime,
        mentions: int,
        score_sum: float,
        confidence_sum: float,
        weighted_score_sum: float,
        engagement_sum: float,
    ) -> None:
        key = (symbol, kind, bucket_start)
        cur = self.aggregates.get(key)
        inc = {
            "mentions": mentions,
            "score_sum": score_sum,
            "confidence_sum": confidence_sum,
            "weighted_score_sum": weighted_score_sum,
            "engagement_sum": engagement_sum,
        }
        if cur is None:
            self.aggregates[key] = inc
        else:
            for k, v in inc.items():
                cur[k] += v
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_content_repository.py -v`
Expected: PASS (all tests, including the unchanged insert/fetch/mark ones)

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/repository.py tests/test_content_repository.py
git commit -m "refactor(sources): additive upsert_aggregate (sums, hourly bucket key)"
```

---

## Task 3: Worker writes additive hourly buckets

**Files:**
- Modify: `services/sentiment-service/app/worker.py` — `run_once` (`:56-98`), drop `WINDOW_SIZE`
- Test: `tests/test_sentiment_worker.py` (exists — add a bucket-internals test; the module is already loaded via `importlib` as `sw` at the top of the file)

- [ ] **Step 1: Add a failing additive-bucket test**

The existing `tests/test_sentiment_worker.py` already loads the worker module as `sw` (via `importlib.util.spec_from_file_location`) and defines `FakeScorer`/`FakeProducer`. Its two current tests only assert `repo.aggregates` is truthy, so they keep passing. Append a new test that asserts the bucket sums, plus a `_floor_hour` import. At the end of the file add:

```python
async def test_worker_accumulates_additive_bucket() -> None:
    from datetime import datetime, timezone

    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, 10, 30, tzinfo=timezone.utc)
    for ext in ("a", "b"):
        await repo.insert_items([RawItem(
            source="bluesky", kind="social", external_id=ext, text="$BTC up",
            symbols=["BTC"], engagement=2.0, published_at=ts,
        )])
    # FakeScorer returns score=0.8, confidence=0.9 for text containing "up".
    worker = sw.SentimentDbWorker(repo, FakeScorer(), FakeProducer(), batch=10)

    await worker.run_once()

    bucket = repo.aggregates[("BTC", "social", sw._floor_hour(ts))]
    assert bucket["mentions"] == 2
    assert bucket["score_sum"] == pytest.approx(1.6)           # 0.8 + 0.8
    assert bucket["confidence_sum"] == pytest.approx(1.8)      # 0.9 + 0.9
    assert bucket["weighted_score_sum"] == pytest.approx(1.44)  # 0.72 + 0.72
    assert bucket["engagement_sum"] == pytest.approx(4.0)      # 2.0 + 2.0
```

Add `import pytest` to the top of the file if it is not already imported.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_sentiment_worker.py -v`
Expected: FAIL — the worker still calls `upsert_aggregate(..., avg_sentiment=...)` (old signature) → `TypeError` (all worker tests fail until Step 3).

- [ ] **Step 3: Rewrite the worker's aggregate write**

In `services/sentiment-service/app/worker.py`:

Delete `WINDOW_SIZE = 3600` (`:29`). Replace the per-symbol loop body inside `run_once` (`:68-96`) with:

```python
            symbols = row.symbols or ["MARKET"]
            bucket_start = _floor_hour(row.published_at)
            engagement = float(row.engagement or 0.0)
            for symbol in symbols:
                await self._repo.upsert_aggregate(
                    symbol=symbol,
                    kind=row.kind,
                    bucket_start=bucket_start,
                    mentions=1,
                    score_sum=result.score,
                    confidence_sum=result.confidence,
                    weighted_score_sum=result.score * result.confidence,
                    engagement_sum=engagement,
                )
                await self._producer.publish(
                    Topic.SENTIMENT,
                    SentimentEvent(
                        symbol=symbol,
                        sentiment_score=result.score,
                        confidence=result.confidence,
                        model_name=result.model_name,
                        input_kind=row.kind,
                        sample_size=1,
                        meta={"source": row.source},
                    ),
                )
                EVENTS_PRODUCED.labels(
                    SERVICE, Topic.SENTIMENT.value, "SentimentEvent"
                ).inc()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_sentiment_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/sentiment-service/app/worker.py tests/test_sentiment_worker.py
git commit -m "feat(sentiment): worker writes additive hourly buckets"
```

---

## Task 4: Continuous HF sentiment score

**Files:**
- Modify: `services/sentiment-service/app/scorer.py` — `_score_hf` (`:70-83`)
- Test: `tests/test_sentiment.py` (exists — loads the scorer module as `scorer_mod` via `importlib`; the lexicon tests stay, add HF-path tests)

- [ ] **Step 1: Add failing continuous-score tests**

`tests/test_sentiment.py` already loads the scorer as `scorer_mod`. Append these tests (they drive a fake HF pipeline through the `_score_hf` path):

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_sentiment.py -v`
Expected: FAIL on the two new tests — current `_score_hf` returns `signed * best["score"]` (0.7), not 0.6. Existing lexicon tests still PASS.

- [ ] **Step 3: Rewrite `_score_hf` for a continuous score**

Replace `_score_hf` (`scorer.py:70-83`) with:

```python
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
```

Note: `_get` scans label substrings so it tolerates `Bullish`/`positive`/`LABEL_pos` variants, matching the existing tolerant matching in the old code.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_sentiment.py -v`
Expected: PASS (lexicon + new HF tests)

- [ ] **Step 5: Commit**

```bash
git add services/sentiment-service/app/scorer.py tests/test_sentiment.py
git commit -m "feat(sentiment): continuous HF score (P(bull)-P(bear))"
```

---

## Task 5: `SentimentAggReader` — window derivation + decay + series + authors

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/reader.py`
- Modify: `libs/cmi_common/cmi_common/sources/__init__.py`
- Test: `tests/test_sentiment_reader_pure.py` (create — pure helpers only; SQL integration is Task 8)

The reader has two pieces: pure helpers (window→timedelta, bucket-decay math) that are unit-tested here, and async SQL methods integration-tested in Task 8. Keeping the math pure lets us test the decay/mean logic without a DB.

- [ ] **Step 1: Write the failing pure-helper test**

Create `tests/test_sentiment_reader_pure.py`:

```python
"""Pure window/decay math for the sentiment reader (no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cmi_common.sources.reader import (
    WINDOWS,
    BucketRow,
    aggregate_buckets,
    window_delta,
)


def test_window_delta_known_windows() -> None:
    assert window_delta("24h") == timedelta(hours=24)
    assert window_delta("7d") == timedelta(days=7)
    assert window_delta("6mo") == timedelta(days=182)
    assert window_delta("5y") == timedelta(days=1825)


def test_windows_constant_matches_approved_set() -> None:
    assert WINDOWS == ("1h", "24h", "7d", "30d", "6mo", "1y", "5y")


def test_aggregate_no_decay_is_plain_mean() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    rows = [
        BucketRow(now - timedelta(hours=1), mentions=2, score_sum=1.0,
                  confidence_sum=1.6, weighted_score_sum=0.8, engagement_sum=4.0),
        BucketRow(now - timedelta(hours=2), mentions=1, score_sum=-0.5,
                  confidence_sum=0.5, weighted_score_sum=-0.25, engagement_sum=1.0),
    ]
    out = aggregate_buckets(rows, now=now, half_life_h=None)
    assert out["mentions"] == 3
    assert out["avg"] == pytest.approx((1.0 - 0.5) / 3)
    assert out["weighted_avg"] == pytest.approx((0.8 - 0.25) / (1.6 + 0.5))
    assert out["engagement"] == pytest.approx(5.0)


def test_aggregate_empty_is_zeros() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    out = aggregate_buckets([], now=now, half_life_h=None)
    assert out == {"mentions": 0, "avg": 0.0, "weighted_avg": 0.0, "engagement": 0.0}


def test_aggregate_decay_weights_recent_more() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    recent = BucketRow(now - timedelta(hours=1), mentions=1, score_sum=1.0,
                       confidence_sum=1.0, weighted_score_sum=1.0, engagement_sum=0.0)
    old = BucketRow(now - timedelta(hours=100), mentions=1, score_sum=-1.0,
                    confidence_sum=1.0, weighted_score_sum=-1.0, engagement_sum=0.0)
    out = aggregate_buckets([recent, old], now=now, half_life_h=1.0)
    # 1h half-life crushes the 100h-old bearish bucket → weighted_avg strongly positive
    assert out["weighted_avg"] > 0.9
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_sentiment_reader_pure.py -v`
Expected: FAIL — `ModuleNotFoundError: cmi_common.sources.reader`

- [ ] **Step 3: Create the reader with pure helpers + async SQL methods**

Create `libs/cmi_common/cmi_common/sources/reader.py`:

```python
"""Read-side derivation over content_sentiment_agg hourly buckets.

Pure helpers (window_delta, aggregate_buckets) hold the window/decay math and
are unit-tested without a DB. SqlSentimentAggReader runs the range queries and
the read-time distinct-author count; it is integration-tested against Postgres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ContentSentimentAgg, RawContent

WINDOWS: tuple[str, ...] = ("1h", "24h", "7d", "30d", "6mo", "1y", "5y")

_WINDOW_DELTAS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "6mo": timedelta(days=182),
    "1y": timedelta(days=365),
    "5y": timedelta(days=1825),
}

# Distinct-author read is a bounded scan on raw_content; refuse long windows.
_AUTHORS_MAX = timedelta(days=30)


def window_delta(window: str) -> timedelta:
    try:
        return _WINDOW_DELTAS[window]
    except KeyError as exc:
        raise ValueError(f"unknown window {window!r}") from exc


@dataclass(slots=True)
class BucketRow:
    bucket_start: datetime
    mentions: int
    score_sum: float
    confidence_sum: float
    weighted_score_sum: float
    engagement_sum: float


def _decay(bucket_start: datetime, now: datetime, half_life_h: float) -> float:
    age_h = max(0.0, (now - bucket_start).total_seconds() / 3600.0)
    return math.exp(-age_h / half_life_h)


def aggregate_buckets(
    rows: list[BucketRow], *, now: datetime, half_life_h: float | None
) -> dict[str, float]:
    """Collapse buckets into {mentions, avg, weighted_avg, engagement}.

    half_life_h None → plain sums. Otherwise each bucket is weighted by
    exp(-age_h / half_life_h) for the sentiment means (counts stay integer).
    """
    mentions = sum(r.mentions for r in rows)
    engagement = sum(r.engagement_sum for r in rows)
    if not rows:
        return {"mentions": 0, "avg": 0.0, "weighted_avg": 0.0, "engagement": 0.0}

    if half_life_h is None:
        score_sum = sum(r.score_sum for r in rows)
        conf_sum = sum(r.confidence_sum for r in rows)
        wscore_sum = sum(r.weighted_score_sum for r in rows)
    else:
        score_sum = conf_sum = wscore_sum = 0.0
        for r in rows:
            d = _decay(r.bucket_start, now, half_life_h)
            score_sum += r.score_sum * d
            conf_sum += r.confidence_sum * d
            wscore_sum += r.weighted_score_sum * d

    avg = score_sum / mentions if mentions else 0.0
    weighted_avg = wscore_sum / conf_sum if conf_sum else 0.0
    return {
        "mentions": mentions,
        "avg": round(avg, 4),
        "weighted_avg": round(weighted_avg, 4),
        "engagement": round(engagement, 4),
    }


class SqlSentimentAggReader:
    """AsyncSession-backed reader over content_sentiment_agg / raw_content."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _fetch_buckets(
        self, *, symbol: str | None, kind: str | None, since: datetime
    ) -> list[BucketRow]:
        m = ContentSentimentAgg
        stmt = select(
            m.bucket_start, m.mentions, m.score_sum, m.confidence_sum,
            m.weighted_score_sum, m.engagement_sum,
        ).where(m.bucket_start >= since)
        if symbol is not None:
            stmt = stmt.where(m.symbol == symbol)
        if kind is not None and kind != "all":
            stmt = stmt.where(m.kind == kind)
        rows = (await self._session.execute(stmt)).all()
        return [
            BucketRow(bucket_start=r[0], mentions=r[1], score_sum=r[2],
                      confidence_sum=r[3], weighted_score_sum=r[4], engagement_sum=r[5])
            for r in rows
        ]

    async def window_stats(
        self,
        *,
        symbol: str | None,
        kind: str | None,
        window: str,
        half_life_h: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, float]:
        now = now or datetime.now(tz=UTC)
        since = now - window_delta(window)
        rows = await self._fetch_buckets(symbol=symbol, kind=kind, since=since)
        out = aggregate_buckets(rows, now=now, half_life_h=half_life_h)
        out["window"] = window
        return out

    async def all_windows(
        self, *, symbol: str | None, kind: str | None,
        half_life_h: float | None = None, now: datetime | None = None,
    ) -> list[dict[str, float]]:
        now = now or datetime.now(tz=UTC)
        return [
            await self.window_stats(symbol=symbol, kind=kind, window=w,
                                    half_life_h=half_life_h, now=now)
            for w in WINDOWS
        ]

    async def series(
        self, *, symbol: str | None, kind: str | None, points: int,
        now: datetime | None = None,
    ) -> list[dict[str, float]]:
        """Last `points` hourly buckets, oldest first, as {hour, sentiment}."""
        now = now or datetime.now(tz=UTC)
        since = now - timedelta(hours=points)
        rows = await self._fetch_buckets(symbol=symbol, kind=kind, since=since)
        by_hour: dict[datetime, list[BucketRow]] = {}
        for r in rows:
            by_hour.setdefault(r.bucket_start, []).append(r)
        out = []
        for hour in sorted(by_hour):
            agg = aggregate_buckets(by_hour[hour], now=now, half_life_h=None)
            out.append({"hour": hour.isoformat(), "sentiment": agg["avg"],
                        "mentions": agg["mentions"]})
        return out

    async def distinct_authors(
        self, *, symbol: str, window: str, now: datetime | None = None
    ) -> int:
        delta = window_delta(window)
        if delta > _AUTHORS_MAX:
            raise ValueError("distinct authors only for windows <= 30d")
        now = now or datetime.now(tz=UTC)
        since = now - delta
        stmt = (
            select(func.count(distinct(RawContent.author)))
            .where(RawContent.author.is_not(None))
            .where(RawContent.published_at >= since)
            .where(RawContent.symbols.contains([symbol]))  # JSONB @>
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)
```

- [ ] **Step 4: Export the reader**

In `libs/cmi_common/cmi_common/sources/__init__.py`, add to the imports and `__all__`:

```python
from .reader import (
    WINDOWS,
    BucketRow,
    SqlSentimentAggReader,
    aggregate_buckets,
    window_delta,
)
```

Add `"WINDOWS"`, `"BucketRow"`, `"SqlSentimentAggReader"`, `"aggregate_buckets"`, `"window_delta"` to the `__all__` list (keep it alphabetically sorted like the existing entries).

- [ ] **Step 5: Run the pure test to verify it passes**

Run: `python -m pytest tests/test_sentiment_reader_pure.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/reader.py libs/cmi_common/cmi_common/sources/__init__.py tests/test_sentiment_reader_pure.py
git commit -m "feat(sources): SentimentAggReader (window derivation + decay)"
```

---

## Task 6: api-gateway sentiment endpoints

**Files:**
- Modify: `services/api-gateway/app/read_api.py` — imports + new endpoints near `router` (`:32`)
- Test: `tests/test_api_gateway_sentiment.py` (create)

- [ ] **Step 1: Write the failing endpoint test**

Create `tests/test_api_gateway_sentiment.py`. It exercises the endpoint functions directly with a stub reader (no DB), following the pure-function testing style in the read_api docstring. Reuse the exact path-insert preamble from `tests/test_api_gateway_read.py:17-23` (the api-gateway package is named `app`; sentiment-service tests load their module via `importlib` under unique names, so there is no `app` collision):

```python
"""api-gateway sentiment endpoints delegate to SentimentAggReader."""

from __future__ import annotations

import sys
from pathlib import Path

_SVC = Path(__file__).resolve().parents[1] / "services" / "api-gateway"
if str(_SVC) not in sys.path:
    sys.path.insert(0, str(_SVC))

from app import read_api  # noqa: E402


class _StubReader:
    def __init__(self) -> None:
        self.calls = []

    async def all_windows(self, *, symbol, kind, half_life_h=None):
        self.calls.append(("all_windows", symbol, kind, half_life_h))
        return [{"window": "1h", "avg": 0.5, "weighted_avg": 0.4,
                 "mentions": 3, "engagement": 2.0}]

    async def series(self, *, symbol, kind, points):
        self.calls.append(("series", symbol, kind, points))
        return [{"hour": "2024-01-01T10:00:00+00:00", "sentiment": 0.5, "mentions": 3}]

    async def distinct_authors(self, *, symbol, window):
        self.calls.append(("authors", symbol, window))
        return 7


# _StubReader stands in for SqlSentimentAggReader; endpoints take `reader` as
# their last param so tests bypass the get_reader_dep/Query machinery.
async def test_windows_endpoint_passes_decay() -> None:
    reader = _StubReader()
    out = await read_api.sentiment_windows(
        symbol="btc", kind="all", decay=6.0, reader=reader
    )
    assert out[0]["window"] == "1h"
    assert reader.calls[0] == ("all_windows", "BTC", "all", 6.0)


async def test_series_endpoint_defaults() -> None:
    reader = _StubReader()
    out = await read_api.sentiment_series(symbol="eth", points=12, reader=reader)
    assert out[0]["mentions"] == 3
    assert reader.calls[0] == ("series", "ETH", None, 12)


async def test_authors_endpoint() -> None:
    reader = _StubReader()
    out = await read_api.sentiment_authors(symbol="btc", window="7d", reader=reader)
    assert out == {"symbol": "BTC", "window": "7d", "unique_authors": 7}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_api_gateway_sentiment.py -v`
Expected: FAIL — `AttributeError: module 'read_api' has no attribute 'sentiment_windows'`

- [ ] **Step 3: Add a reader dependency + the three endpoints**

In `services/api-gateway/app/read_api.py`, add to imports (near `:27-30`):

```python
from cmi_common.sources import SqlSentimentAggReader
```

Add a reader dependency provider and endpoints (after the `router` definition, `:32`):

```python
def get_reader_dep(session: AsyncSession = Depends(get_session_dep)) -> SqlSentimentAggReader:
    return SqlSentimentAggReader(session)


@router.get("/api/v1/sentiment/windows")
async def sentiment_windows(
    symbol: str | None = Query(None),
    kind: str = Query("all"),
    decay: float | None = Query(None, gt=0, description="decay half-life in hours"),
    reader: SqlSentimentAggReader = Depends(get_reader_dep),
) -> list[dict]:
    sym = symbol.upper() if symbol else None
    return await reader.all_windows(symbol=sym, kind=kind, half_life_h=decay)


@router.get("/api/v1/sentiment/series")
async def sentiment_series(
    symbol: str | None = Query(None),
    kind: str | None = Query(None),
    points: int = Query(12, ge=1, le=168),
    reader: SqlSentimentAggReader = Depends(get_reader_dep),
) -> list[dict]:
    sym = symbol.upper() if symbol else None
    return await reader.series(symbol=sym, kind=kind, points=points)


@router.get("/api/v1/sentiment/authors")
async def sentiment_authors(
    symbol: str = Query(...),
    window: str = Query("7d"),
    reader: SqlSentimentAggReader = Depends(get_reader_dep),
) -> dict:
    sym = symbol.upper()
    count = await reader.distinct_authors(symbol=sym, window=window)
    return {"symbol": sym, "window": window, "unique_authors": count}
```

Note: the test calls these functions with `reader=<stub>` and `kind`/`points` as plain values, bypassing FastAPI's `Query` defaults — that is why each endpoint takes `reader` as its last param. The `get_reader_dep` provider reuses the already-bound `get_session_dep` override from `main.py`, so no extra wiring is needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_api_gateway_sentiment.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/read_api.py tests/test_api_gateway_sentiment.py
git commit -m "feat(api-gateway): /api/v1/sentiment windows/series/authors endpoints"
```

---

## Task 7: Repoint `compute_content_stats` sentiment onto buckets

**Files:**
- Modify: `services/api-gateway/app/read_api.py` — `compute_content_stats` sentiment block (`:185-226`) and its caller endpoint
- Test: `tests/test_api_gateway_read.py` (adjust any assertion tied to the old sentiment_series computation)

Context: `compute_content_stats` is a pure function over `raw_content` rows that computes both the volume series (kept) and the sentiment series/avg (to be sourced from buckets instead). The cleanest change that preserves the response shape: keep `compute_content_stats` for volume/sources/mentions, and have the *endpoint* overlay `sentiment_series`/`avg_sentiment` from the reader.

- [ ] **Step 1: Find the endpoint that calls `compute_content_stats`**

Run: `grep -rn "compute_content_stats" services/api-gateway/app tests`
Expected: the endpoint (e.g. `content_stats` / `data_stats`) plus existing tests. Read the endpoint to see how it returns the dict.

- [ ] **Step 2: Adjust the failing read test**

In `tests/test_api_gateway_read.py`, find the assertion(s) that check `sentiment_series`/`avg_sentiment` produced by `compute_content_stats` from raw rows. Since the sentiment now comes from the reader (not raw rows), change those to assert the *volume* fields only, and assert `sentiment_series` defaults to an empty list when `compute_content_stats` is called without reader overlay. Concretely, update the pure-function test to:

```python
def test_compute_content_stats_volume_only() -> None:
    # sentiment_series/avg_sentiment are overlaid by the endpoint from the
    # aggregate reader; the pure stats function no longer derives them.
    stats = read_api.compute_content_stats(rows, now=NOW)
    assert "volume_series" in stats
    assert stats["sentiment_series"] == []
    assert stats["avg_sentiment"] == 0.0
```

(Keep whatever `rows`/`NOW` fixtures the existing test already defines.)

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_api_gateway_read.py -v`
Expected: FAIL — current `compute_content_stats` still computes non-empty `sentiment_series` from rows.

- [ ] **Step 4: Strip sentiment from `compute_content_stats`**

In `compute_content_stats` (`read_api.py`), remove the sentiment accumulation: delete the `sent` defaultdict (`:185`), the `score_sum`/`score_n` counters (`:186-187`) and their updates (`:195-197`, `:204-205`), and the `sentiment_series` list-comp (`:215-218`). Set the two output fields to neutral defaults:

```python
        "avg_sentiment": 0.0,
        "sentiment_series": [],
```

Leave volume_series, top_sources, mentions, and counts untouched.

- [ ] **Step 5: Overlay sentiment in the endpoint from the reader**

In the endpoint that returns `compute_content_stats(...)`, inject the reader and overlay the two fields before returning:

```python
    stats = compute_content_stats(rows, now=_utcnow())
    reader = SqlSentimentAggReader(session)
    stats["sentiment_series"] = await reader.series(symbol=None, kind=None, points=12)
    day = await reader.window_stats(symbol=None, kind=None, window="24h")
    stats["avg_sentiment"] = day["avg"]
    return stats
```

Match the endpoint's existing `session` dependency name; if it does not already depend on a session, add `session: AsyncSession = Depends(get_session_dep)`.

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/test_api_gateway_read.py tests/test_api_gateway_sentiment.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/api-gateway/app/read_api.py tests/test_api_gateway_read.py
git commit -m "refactor(api-gateway): source content-stats sentiment from aggregate buckets"
```

---

## Task 8: Alembic migration `0004` + reader integration test

**Files:**
- Create: `migrations/alembic/versions/0004_sentiment_agg_buckets.py`
- Test: `tests/test_sentiment_reader_sql.py` (create; skips cleanly without a DB)

- [ ] **Step 1: Write the migration**

Create `migrations/alembic/versions/0004_sentiment_agg_buckets.py`:

```python
"""content_sentiment_agg → additive hourly buckets + raw_content author indexes

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No readers of the old table and its data is recomputable → drop/recreate.
    op.drop_table("content_sentiment_agg")
    op.create_table(
        "content_sentiment_agg",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("mentions", sa.Integer, server_default="0"),
        sa.Column("score_sum", sa.Float, server_default="0"),
        sa.Column("confidence_sum", sa.Float, server_default="0"),
        sa.Column("weighted_score_sum", sa.Float, server_default="0"),
        sa.Column("engagement_sum", sa.Float, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    # Support the read-time distinct-author query (symbols @> '["BTC"]').
    op.create_index(
        "ix_raw_content_symbols_gin", "raw_content", ["symbols"],
        postgresql_using="gin",
    )
    op.create_index("ix_raw_content_published_at", "raw_content", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_raw_content_published_at", table_name="raw_content")
    op.drop_index("ix_raw_content_symbols_gin", table_name="raw_content")
    op.drop_table("content_sentiment_agg")
    op.create_table(
        "content_sentiment_agg",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("window_size", sa.Integer, primary_key=True),
        sa.Column("mentions", sa.Integer, server_default="0"),
        sa.Column("unique_authors", sa.Integer, server_default="0"),
        sa.Column("engagement_sum", sa.Float, server_default="0"),
        sa.Column("avg_sentiment", sa.Float, server_default="0"),
        sa.Column("weighted_sentiment", sa.Float, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
```

- [ ] **Step 2: Verify the migration script imports and chains**

Run: `python -c "import importlib.util, pathlib; p=pathlib.Path('migrations/alembic/versions/0004_sentiment_agg_buckets.py'); s=importlib.util.spec_from_file_location('m0004', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.revision, m.down_revision)"`
Expected: `0004 0003`

- [ ] **Step 3: Write a DB-gated reader integration test**

Create `tests/test_sentiment_reader_sql.py`. It seeds buckets and asserts derivation + decay against a real session, but skips when no DB is configured so the default unit run stays DB-free:

```python
"""Integration: SqlSentimentAggReader over seeded content_sentiment_agg."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("CMI_TEST_DB_URL"),
    reason="set CMI_TEST_DB_URL to run reader integration tests",
)


@pytest.fixture()
async def session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from cmi_common.db.models import Base

    engine = create_async_engine(os.environ["CMI_TEST_DB_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(session, symbol, bucket_start, *, score_sum, conf_sum, wsum, n):
    from cmi_common.db.models import ContentSentimentAgg

    session.add(ContentSentimentAgg(
        symbol=symbol, kind="social", bucket_start=bucket_start, mentions=n,
        score_sum=score_sum, confidence_sum=conf_sum,
        weighted_score_sum=wsum, engagement_sum=0.0,
    ))
    await session.commit()


async def test_window_derivation_24h_vs_7d(session) -> None:
    from cmi_common.sources import SqlSentimentAggReader

    now = datetime(2024, 1, 10, tzinfo=timezone.utc)
    await _seed(session, "BTC", now - timedelta(hours=2),
               score_sum=1.0, conf_sum=1.0, wsum=1.0, n=1)   # in 24h
    await _seed(session, "BTC", now - timedelta(days=3),
               score_sum=-1.0, conf_sum=1.0, wsum=-1.0, n=1)  # only in 7d

    reader = SqlSentimentAggReader(session)
    d1 = await reader.window_stats(symbol="BTC", kind="all", window="24h", now=now)
    d7 = await reader.window_stats(symbol="BTC", kind="all", window="7d", now=now)

    assert d1["mentions"] == 1 and d1["avg"] == pytest.approx(1.0)
    assert d7["mentions"] == 2 and d7["avg"] == pytest.approx(0.0)
```

- [ ] **Step 4: Run the DB-gated test (skips without a DB)**

Run: `python -m pytest tests/test_sentiment_reader_sql.py -v`
Expected: SKIPPED (1 skipped) unless `CMI_TEST_DB_URL` is set; if a test DB is available, run `CMI_TEST_DB_URL=postgresql+asyncpg://... python -m pytest tests/test_sentiment_reader_sql.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/alembic/versions/0004_sentiment_agg_buckets.py tests/test_sentiment_reader_sql.py
git commit -m "feat(db): migration 0004 additive sentiment buckets + author indexes"
```

---

## Task 9: Realign the frontend `content_sentiment_agg` type

**Files:**
- Modify: `frontend/src/lib/types/content.ts`

- [ ] **Step 1: Inspect the current type**

Run: `grep -n "content_sentiment_agg\|window_size\|window_start\|unique_authors\|avg_sentiment\|weighted_sentiment" frontend/src/lib/types/content.ts`
Expected: a TS interface mirroring the old columns.

- [ ] **Step 2: Update the interface to the new schema**

Replace the old fields (`window_start`, `window_size`, `unique_authors`, `avg_sentiment`, `weighted_sentiment`) with the additive-bucket shape. Use the existing interface name; adjust to:

```typescript
export interface ContentSentimentAgg {
  symbol: string;
  kind: string;
  bucket_start: string;   // ISO timestamp (hourly bucket)
  mentions: number;
  score_sum: number;
  confidence_sum: number;
  weighted_score_sum: number;
  engagement_sum: number;
  updated_at: string;
}
```

If the type is consumed anywhere expecting `avg_sentiment`/`window_size` (grep for it), update those call sites to derive `avg = score_sum / mentions` or to consume the new `/api/v1/sentiment/windows` shape instead. If there are no consumers, this is type-only.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run build` (or `npx tsc --noEmit` if the project exposes it)
Expected: no type errors referencing `content_sentiment_agg` fields.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types/content.ts
git commit -m "refactor(frontend): realign content_sentiment_agg type to bucket schema"
```

---

## Task 10: Full suite, lint, and final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole Python test suite**

Run: `python -m pytest tests -v`
Expected: PASS (reader SQL test SKIPPED without a DB). Zero failures.

- [ ] **Step 2: Lint the changed Python**

Run: `make lint` (ruff + black --check + mypy)
Expected: clean. Fix any ruff/black/mypy findings in the files touched (Tasks 1-8), then re-run.

- [ ] **Step 3: Grep for stale references to the old schema**

Run: `grep -rn "window_size\|weighted_sentiment\|avg_sentiment\|unique_authors" libs services --include=*.py`
Expected: no hits in `libs/cmi_common/cmi_common/sources`, `services/sentiment-service`, or `services/api-gateway` (the `Sentiment` hypertable's own `sentiment_score` is unrelated and fine). Any remaining hit is a missed migration site — fix it.

- [ ] **Step 4: Optional live smoke (if the stack is up)**

Run: `make up && make migrate`, then once services are healthy:
`curl -s 'http://api.cmi.localhost/api/v1/sentiment/windows?symbol=BTC' | head`
Expected: JSON array of 7 window objects (`1h`..`5y`), zeros if no scored content yet.

- [ ] **Step 5: Final commit (if lint produced fixes)**

```bash
git add -A
git commit -m "chore(sentiment): lint fixes for aggregation rework"
```

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** additive buckets (T1-2), worker (T3), continuous score (T4), reader+windows+decay (T5), endpoints (T6), repoint content-stats (T7), migration+authors indexes+integration (T8), frontend type (T9), verification (T10). All spec sections mapped.
- **Deferred items honored:** no `day`/compaction tier, no `postgres-hll`, distinct authors capped at 30d (T5 `_AUTHORS_MAX`).
- **Type consistency:** `upsert_aggregate(symbol, kind, bucket_start, mentions, score_sum, confidence_sum, weighted_score_sum, engagement_sum)` identical across Protocol/Sql/Fake/worker/tests; `BucketRow` fields match the SELECT projection and `aggregate_buckets`; reader method names (`all_windows`, `series`, `distinct_authors`, `window_stats`) match the endpoint call sites and the stub in T6.
- **Import patterns (verified against the codebase):** sentiment-service modules are loaded via `importlib.util.spec_from_file_location` under unique names — `tests/test_sentiment_worker.py` (`sw`) and `tests/test_sentiment.py` (`scorer_mod`) already exist and are extended, not created. The api-gateway package is `app`, imported via the `sys.path` insert in `tests/test_api_gateway_read.py`. `cmi_common` is on `pythonpath` (per `pyproject.toml`) so reader tests import it directly. No `app` name collision because sentiment tests never import bare `app`.
```
