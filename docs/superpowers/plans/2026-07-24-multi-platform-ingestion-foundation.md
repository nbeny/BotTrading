# Multi-Platform Ingestion Foundation — Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Kafka-cascade collection model with DB-sourced ingestion: providers fan out into independent self-throttling poll loops that persist raw items to Postgres, and `sentiment-service` scores unprocessed rows from the DB (still publishing `SentimentEvent` to Kafka). This plan delivers the working pipeline with the **four existing sources** (Bluesky, Reddit, CryptoCompare, RSS); Plan 2 adds the remaining free platforms on the same pattern.

**Architecture:** New `cmi_common/sources` primitives — `RawItem`, an evolved `Provider` protocol (`fetch() -> list[RawItem]`), `parse_retry_after`, `AdaptivePollLoop` (poll → persist → pause-until-reset on `RateLimitedError`, no failover), and `ContentRepository` (dedup insert + unscored queue + aggregate upsert). Two fan-out services run one loop per provider writing to `raw_content`. `sentiment-service` runs a DB worker that scores rows, upserts `content_sentiment_agg`, and publishes `SentimentEvent`. `SourceCascade` is retired.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async + asyncpg, Alembic, TimescaleDB, httpx, respx (tests), Redis (`Cache`), aiokafka, Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-07-24-multi-platform-ingestion-db-sentiment-design.md`

---

## File Structure

**Shared (`libs/cmi_common/cmi_common/`):**
- `sources/raw.py` — `RawItem` Pydantic model (the normalized item every provider returns)
- `sources/provider.py` — evolved `Provider` protocol + `parse_retry_after`
- `sources/loop.py` — `AdaptivePollLoop`
- `sources/repository.py` — `ContentRepository` protocol, `SqlContentRepository`, `FakeContentRepository`
- `sources/cascade.py` — MODIFY: keep `RateLimitedError` + `CircuitBreaker`; delete `Provider` (moved) + `SourceCascade`
- `sources/__init__.py` — MODIFY exports
- `db/models.py` — MODIFY: add `RawContent`, `ContentSentimentAgg` ORM models + hypertable entry

**Migration:** `migrations/alembic/versions/0003_raw_content.py`

**Services:**
- `services/collector-social/app/main.py` — MODIFY: fan-out `AdaptivePollLoop` per provider → DB
- `services/collector-social/app/providers/{bluesky,reddit}.py` — MODIFY: return `RawItem`
- `services/collector-news/app/main.py` — MODIFY: fan-out per provider → DB
- `services/collector-news/app/providers/{cryptocompare,rss}.py` — MODIFY: return `RawItem`
- `services/sentiment-service/app/worker.py` — CREATE: DB poll→score→aggregate→publish
- `services/sentiment-service/app/main.py` — MODIFY: run worker instead of Kafka consumer
- `services/{collector-social,collector-news,sentiment-service}/pyproject.toml` — MODIFY: add `sqlalchemy`, `asyncpg` deps

**Tests:** `tests/test_raw_item.py`, `test_parse_retry_after.py`, `test_adaptive_poll_loop.py`, `test_content_repository.py`, `test_bluesky_provider.py` (update), `test_reddit_provider.py` (update), `test_cryptocompare_news_provider.py` (update), `test_rss_provider.py` (update), `test_source_cascade.py` (delete), `test_sentiment_worker.py`

---

## Phase A — Framework (`cmi_common`)

### Task 1: `RawItem` model

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/raw.py`
- Test: `tests/test_raw_item.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_raw_item.py`:

```python
"""RawItem: normalized item every provider returns."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cmi_common.sources import RawItem


def test_minimal_social_item() -> None:
    item = RawItem(source="bluesky", kind="social", external_id="at://1", text="$BTC up")
    assert item.symbols == []
    assert item.title is None
    assert item.engagement is None


def test_full_news_item() -> None:
    item = RawItem(
        source="rss", kind="news", external_id="guid-1",
        title="BTC rallies", text="body", url="https://x/a",
        symbols=["BTC"], published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert item.kind == "news"
    assert item.symbols == ["BTC"]


def test_kind_must_be_social_or_news() -> None:
    with pytest.raises(ValidationError):
        RawItem(source="x", kind="video", external_id="1")


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        RawItem(source="x", kind="news", external_id="1", bogus=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raw_item.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'RawItem'`

- [ ] **Step 3: Implement**

Create `libs/cmi_common/cmi_common/sources/raw.py`:

```python
"""RawItem — the normalized unit every provider yields.

One social post or one news article. Persisted verbatim to ``raw_content`` and
later scored by the sentiment worker. Deliberately provider-agnostic: each
provider maps its API payload onto these fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    kind: Literal["social", "news"]
    # Provider-stable id used for cross-restart dedup via (source, external_id).
    external_id: str
    text: str = ""
    title: str | None = None
    url: str | None = None
    author: str | None = None
    symbols: list[str] = Field(default_factory=list)
    engagement: float | None = None
    lang: str | None = None
    published_at: datetime | None = None
```

Add to `libs/cmi_common/cmi_common/sources/__init__.py` (keep existing exports for now):

```python
from .raw import RawItem
```
and add `"RawItem"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raw_item.py -q -p no:cacheprovider`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/raw.py libs/cmi_common/cmi_common/sources/__init__.py tests/test_raw_item.py
git commit -m "feat(cmi_common): add RawItem normalized ingestion model"
```

---

### Task 2: `parse_retry_after` + evolved `Provider` protocol

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/provider.py`
- Test: `tests/test_parse_retry_after.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_retry_after.py`:

```python
"""parse_retry_after: derive resume-after seconds from rate-limit headers."""

from __future__ import annotations

import httpx

from cmi_common.sources import parse_retry_after


def _resp(headers: dict[str, str]) -> httpx.Response:
    return httpx.Response(429, headers=headers)


def test_retry_after_seconds() -> None:
    assert parse_retry_after(_resp({"Retry-After": "42"}), default=99) == 42.0


def test_x_ratelimit_reset_delta_seconds() -> None:
    # A small value is treated as a delta in seconds, not an epoch.
    assert parse_retry_after(_resp({"x-ratelimit-reset": "30"}), default=99) == 30.0


def test_no_headers_uses_default() -> None:
    assert parse_retry_after(_resp({}), default=77) == 77.0


def test_retry_after_takes_priority_over_reset() -> None:
    r = _resp({"Retry-After": "10", "x-ratelimit-reset": "999"})
    assert parse_retry_after(r, default=99) == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parse_retry_after.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'parse_retry_after'`

- [ ] **Step 3: Implement**

Create `libs/cmi_common/cmi_common/sources/provider.py`:

```python
"""Provider protocol + rate-limit header parsing.

A provider is one platform's poller. It knows how to call its API and map the
result to ``RawItem`` list, declares its rate-limit budget, and raises
``RateLimitedError`` when throttled. It owns no DB/Kafka knowledge — the
``AdaptivePollLoop`` drives it and persists its output.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from .raw import RawItem


@runtime_checkable
class Provider(Protocol):
    #: stable platform id (also the ``raw_content.source`` value + throttle key)
    name: str
    #: "social" | "news"
    kind: str
    #: (max_calls, window_seconds) proactive budget for the Redis token bucket
    rate_limit: tuple[int, int]

    async def fetch(self) -> list[RawItem]:
        ...

    async def close(self) -> None:
        ...


def parse_retry_after(response: httpx.Response, *, default: float) -> float:
    """Seconds to wait before retrying, learned from the API's own headers.

    Priority: ``Retry-After`` (delta-seconds) → ``X-RateLimit-Reset``
    (delta-seconds; large values interpreted as an epoch are still bounded by
    the caller) → ``default``. HTTP-date ``Retry-After`` is not emitted by the
    crypto APIs we use, so only the numeric form is handled; anything
    unparseable falls back to ``default``.
    """
    ra = response.headers.get("retry-after")
    if ra is not None:
        try:
            return float(ra)
        except ValueError:
            return default
    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            return float(reset)
        except ValueError:
            return default
    return default
```

Add to `sources/__init__.py`:

```python
from .provider import Provider, parse_retry_after
```
Add `"Provider"` and `"parse_retry_after"` to `__all__`. **Remove** the `Provider` re-export from `.cascade` in `__init__.py` (it is redefined here in Task 4).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parse_retry_after.py -q -p no:cacheprovider`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/provider.py libs/cmi_common/cmi_common/sources/__init__.py tests/test_parse_retry_after.py
git commit -m "feat(cmi_common): add evolved Provider protocol + parse_retry_after"
```

---

### Task 3: Retire `SourceCascade`, keep `RateLimitedError` + `CircuitBreaker`

**Files:**
- Modify: `libs/cmi_common/cmi_common/sources/cascade.py`
- Modify: `libs/cmi_common/cmi_common/sources/__init__.py`
- Delete: `tests/test_source_cascade.py`
- Test: `tests/test_circuit_breaker.py` (unchanged — must still pass)

- [ ] **Step 1: Delete the cascade test and trim the module**

```bash
git rm tests/test_source_cascade.py
```

Edit `libs/cmi_common/cmi_common/sources/cascade.py`: keep the module docstring, `RateLimitedError`, and `CircuitBreaker`. **Delete** the `Provider` Protocol class and the entire `SourceCascade` class, and remove now-unused imports (`Sequence`, `EventProducer`, `Topic`, `EVENTS_PRODUCED`, `BaseEvent`). The file should end after `CircuitBreaker`. Result:

```python
"""Rate-limit primitives shared by the ingestion loops.

``RateLimitedError`` signals a provider is throttled; ``CircuitBreaker`` is a
Redis-backed pause gate (a source stays paused until its cooldown TTL expires,
then the loop probes it again). Used by ``AdaptivePollLoop``.
"""

from __future__ import annotations

import logging

from ..cache import Cache

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised by a provider that has exhausted its quota for now.

    ``retry_after`` (seconds) hints how long to keep the source paused; ``None``
    falls back to the loop/breaker default cooldown.
    """

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class CircuitBreaker:
    """Redis-backed pause gate shared across replicas.

    A tripped source stays paused until its cooldown key TTL expires, after
    which the next poll probes it again.
    """

    def __init__(self, cache: Cache, *, default_cooldown: float = 300.0) -> None:
        self._cache = cache
        self._default = default_cooldown

    async def is_open(self, name: str) -> bool:
        return bool(await self._cache.client.exists(f"cb:{name}"))

    async def trip(self, name: str, cooldown: float | None = None) -> None:
        ttl = max(1, int(cooldown if cooldown is not None else self._default))
        await self._cache.client.set(f"cb:{name}", "1", ex=ttl)
```

- [ ] **Step 2: Update exports**

Set `libs/cmi_common/cmi_common/sources/__init__.py` to:

```python
"""Free-tier ingestion: providers, adaptive rate-limited poll loops, persistence."""

from __future__ import annotations

from .cascade import CircuitBreaker, RateLimitedError
from .loop import AdaptivePollLoop
from .provider import Provider, parse_retry_after
from .raw import RawItem
from .repository import ContentRepository, FakeContentRepository, SqlContentRepository

__all__ = [
    "AdaptivePollLoop",
    "CircuitBreaker",
    "ContentRepository",
    "FakeContentRepository",
    "Provider",
    "RateLimitedError",
    "RawItem",
    "SqlContentRepository",
    "parse_retry_after",
    "raw_item_to_row",
]
```

> Note: this imports `loop` and `repository` (created in Tasks 4–5) and `raw_item_to_row` (Task 4). This task's tests only exercise `test_circuit_breaker.py`; the full `__init__` import will fail until Tasks 4–5 land. To keep THIS task green in isolation, temporarily comment the `loop`/`repository` imports and their `__all__` entries, then restore them in Task 5's final step. (Same staging trick as the prior cascade plan.)

Task-3 interim `__init__.py`:

```python
"""Free-tier ingestion primitives."""

from __future__ import annotations

from .cascade import CircuitBreaker, RateLimitedError
from .provider import Provider, parse_retry_after
from .raw import RawItem

__all__ = ["CircuitBreaker", "Provider", "RateLimitedError", "RawItem", "parse_retry_after"]
```

- [ ] **Step 3: Verify breaker + earlier tests pass, cascade import is gone**

Run: `python -m pytest tests/test_circuit_breaker.py tests/test_raw_item.py tests/test_parse_retry_after.py -q -p no:cacheprovider`
Expected: PASS. Also run `python -m pytest tests/ -q -p no:cacheprovider -k "cascade"` → 0 selected (deleted).

- [ ] **Step 4: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/cascade.py libs/cmi_common/cmi_common/sources/__init__.py tests/test_source_cascade.py
git commit -m "refactor(cmi_common): retire SourceCascade, keep rate-limit primitives"
```

---

### Task 4: `ContentRepository` protocol, `raw_item_to_row`, `FakeContentRepository`

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/repository.py`
- Test: `tests/test_content_repository.py`

- [ ] **Step 1: Write the failing test** (pure mapping + fake behavior; real SQL is integration-tested in Task 6)

Create `tests/test_content_repository.py`:

```python
"""raw_item_to_row mapping + FakeContentRepository dedup/queue/aggregate."""

from __future__ import annotations

from datetime import datetime, timezone

from cmi_common.sources import FakeContentRepository, RawItem
from cmi_common.sources.repository import raw_item_to_row


def _item(**kw) -> RawItem:
    base = dict(source="bluesky", kind="social", external_id="1", text="$BTC")
    base.update(kw)
    return RawItem(**base)


def test_raw_item_to_row_maps_fields() -> None:
    row = raw_item_to_row(_item(symbols=["BTC"], engagement=3.0))
    assert row["source"] == "bluesky"
    assert row["external_id"] == "1"
    assert row["symbols"] == ["BTC"]
    assert row["engagement"] == 3.0
    assert row["scored_at"] is None


async def test_fake_insert_dedups_on_source_external_id() -> None:
    repo = FakeContentRepository()
    n1 = await repo.insert_items([_item(external_id="a"), _item(external_id="b")])
    n2 = await repo.insert_items([_item(external_id="a")])  # duplicate
    assert n1 == 2
    assert n2 == 0
    assert len(repo.rows) == 2


async def test_fake_fetch_unscored_and_mark() -> None:
    repo = FakeContentRepository()
    await repo.insert_items([_item(external_id="a"), _item(external_id="b")])
    unscored = await repo.fetch_unscored(limit=10)
    assert len(unscored) == 2
    await repo.mark_scored(unscored[0].id, score=0.5, confidence=0.8, model="m")
    assert len(await repo.fetch_unscored(limit=10)) == 1


async def test_fake_upsert_aggregate_accumulates() -> None:
    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await repo.upsert_aggregate(
        symbol="BTC", kind="social", window_start=ts, window_size=3600,
        mentions=1, unique_authors=1, engagement_sum=2.0, avg_sentiment=0.5,
        weighted_sentiment=0.4,
    )
    key = ("BTC", "social", ts, 3600)
    assert repo.aggregates[key]["mentions"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_content_repository.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError` (repository not present)

- [ ] **Step 3: Implement**

Create `libs/cmi_common/cmi_common/sources/repository.py`:

```python
"""Persistence for raw ingested content + the sentiment aggregate rollup.

``raw_item_to_row`` is the pure mapping (unit-tested). ``SqlContentRepository``
is the async SQLAlchemy implementation (integration-tested against Postgres).
``FakeContentRepository`` is an in-memory double used by loop/worker unit tests
— it mirrors the same protocol so tests never need a live database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ContentSentimentAgg, RawContent
from .raw import RawItem


def raw_item_to_row(item: RawItem) -> dict[str, Any]:
    """Map a RawItem to a ``raw_content`` insert dict (unscored)."""
    return {
        "source": item.source,
        "kind": item.kind,
        "external_id": item.external_id,
        "url": item.url,
        "author": item.author,
        "title": item.title,
        "text": item.text,
        "symbols": item.symbols,
        "engagement": item.engagement,
        "lang": item.lang,
        "published_at": item.published_at,
        "scored_at": None,
    }


@dataclass
class UnscoredRow:
    """A minimal projection of an unscored raw_content row for the worker."""

    id: int
    source: str
    kind: str
    title: str | None
    text: str
    symbols: list[str]
    engagement: float | None
    published_at: datetime | None


class ContentRepository(Protocol):
    async def insert_items(self, items: list[RawItem]) -> int:
        ...

    async def fetch_unscored(self, limit: int) -> list[UnscoredRow]:
        ...

    async def mark_scored(
        self, row_id: int, *, score: float, confidence: float, model: str
    ) -> None:
        ...

    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        window_start: datetime,
        window_size: int,
        mentions: int,
        unique_authors: int,
        engagement_sum: float,
        avg_sentiment: float,
        weighted_sentiment: float,
    ) -> None:
        ...


class SqlContentRepository:
    """AsyncSession-backed repository. One instance per unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_items(self, items: list[RawItem]) -> int:
        if not items:
            return 0
        rows = [raw_item_to_row(i) for i in items]
        stmt = pg_insert(RawContent).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["source", "external_id"])
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount or 0

    async def fetch_unscored(self, limit: int) -> list[UnscoredRow]:
        stmt = (
            select(RawContent)
            .where(RawContent.scored_at.is_(None))
            .order_by(RawContent.fetched_at)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            UnscoredRow(
                id=r.id, source=r.source, kind=r.kind, title=r.title,
                text=r.text, symbols=list(r.symbols or []),
                engagement=r.engagement, published_at=r.published_at,
            )
            for r in rows
        ]

    async def mark_scored(
        self, row_id: int, *, score: float, confidence: float, model: str
    ) -> None:
        stmt = (
            update(RawContent)
            .where(RawContent.id == row_id)
            .values(
                sentiment_score=score,
                sentiment_confidence=confidence,
                sentiment_model=model,
                scored_at=_utcnow(),
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        window_start: datetime,
        window_size: int,
        mentions: int,
        unique_authors: int,
        engagement_sum: float,
        avg_sentiment: float,
        weighted_sentiment: float,
    ) -> None:
        values = {
            "symbol": symbol, "kind": kind, "window_start": window_start,
            "window_size": window_size, "mentions": mentions,
            "unique_authors": unique_authors, "engagement_sum": engagement_sum,
            "avg_sentiment": avg_sentiment, "weighted_sentiment": weighted_sentiment,
            "updated_at": _utcnow(),
        }
        stmt = pg_insert(ContentSentimentAgg).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "kind", "window_start", "window_size"],
            set_={
                "mentions": ContentSentimentAgg.mentions + mentions,
                "unique_authors": stmt.excluded.unique_authors,
                "engagement_sum": ContentSentimentAgg.engagement_sum + engagement_sum,
                "avg_sentiment": stmt.excluded.avg_sentiment,
                "weighted_sentiment": stmt.excluded.weighted_sentiment,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()


def _utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(tz=timezone.utc)


@dataclass
class FakeContentRepository:
    """In-memory double mirroring ContentRepository for unit tests."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    aggregates: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    async def insert_items(self, items: list[RawItem]) -> int:
        seen = {(r["source"], r["external_id"]) for r in self.rows}
        inserted = 0
        for item in items:
            key = (item.source, item.external_id)
            if key in seen:
                continue
            seen.add(key)
            self._seq += 1
            row = raw_item_to_row(item)
            row["id"] = self._seq
            self.rows.append(row)
            inserted += 1
        return inserted

    async def fetch_unscored(self, limit: int) -> list[UnscoredRow]:
        out = [r for r in self.rows if r["scored_at"] is None][:limit]
        return [
            UnscoredRow(
                id=r["id"], source=r["source"], kind=r["kind"], title=r["title"],
                text=r["text"], symbols=list(r["symbols"]),
                engagement=r["engagement"], published_at=r["published_at"],
            )
            for r in out
        ]

    async def mark_scored(
        self, row_id: int, *, score: float, confidence: float, model: str
    ) -> None:
        for r in self.rows:
            if r["id"] == row_id:
                r["scored_at"] = _utcnow()
                r["sentiment_score"] = score
                return

    async def upsert_aggregate(
        self, *, symbol: str, kind: str, window_start: datetime, window_size: int,
        mentions: int, unique_authors: int, engagement_sum: float,
        avg_sentiment: float, weighted_sentiment: float,
    ) -> None:
        key = (symbol, kind, window_start, window_size)
        cur = self.aggregates.get(key)
        if cur is None:
            self.aggregates[key] = {
                "mentions": mentions, "unique_authors": unique_authors,
                "engagement_sum": engagement_sum, "avg_sentiment": avg_sentiment,
                "weighted_sentiment": weighted_sentiment,
            }
        else:
            cur["mentions"] += mentions
            cur["engagement_sum"] += engagement_sum
            cur["avg_sentiment"] = avg_sentiment
            cur["weighted_sentiment"] = weighted_sentiment
```

> `raw_item_to_row` and `FakeContentRepository` are exported via `sources/__init__.py` (Task 3's final export block); ensure `raw_item_to_row` is importable from `cmi_common.sources.repository` (the test imports it there).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_content_repository.py -q -p no:cacheprovider`
Expected: PASS (4 tests). (Import of `RawContent`/`ContentSentimentAgg` requires Task 5's model — do Task 5 Step "models" first if import fails; these two tasks are paired. If running strictly in order, move the model definitions from Task 5 ahead of this step.)

> **Ordering note:** Task 5 adds the ORM models `RawContent`/`ContentSentimentAgg`. Because `repository.py` imports them, implement the **model additions (Task 5 Steps 3a)** before running this task's tests. Keep the commits separate.

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/repository.py tests/test_content_repository.py
git commit -m "feat(cmi_common): add ContentRepository (+ pure mapping, in-memory fake)"
```

---

### Task 5: ORM models + Alembic migration (`raw_content`, `content_sentiment_agg`)

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py`
- Create: `migrations/alembic/versions/0003_raw_content.py`
- Test: `tests/test_raw_content_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_raw_content_model.py`:

```python
"""RawContent / ContentSentimentAgg ORM shape + hypertable registration."""

from __future__ import annotations

from cmi_common.db.models import HYPERTABLES, ContentSentimentAgg, RawContent


def test_raw_content_columns() -> None:
    cols = set(RawContent.__table__.columns.keys())
    assert {"source", "kind", "external_id", "text", "symbols", "scored_at"} <= cols
    uq = {tuple(sorted(c.columns.keys())) for c in RawContent.__table__.constraints
          if c.__class__.__name__ == "UniqueConstraint"}
    assert ("external_id", "source") in uq


def test_agg_primary_key() -> None:
    pk = {c.name for c in ContentSentimentAgg.__table__.primary_key.columns}
    assert pk == {"symbol", "kind", "window_start", "window_size"}


def test_raw_content_is_hypertable() -> None:
    assert HYPERTABLES.get("raw_content") == "fetched_at"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raw_content_model.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'RawContent'`

- [ ] **Step 3a: Add ORM models**

In `libs/cmi_common/cmi_common/db/models.py`, add these imports at top if missing: `from datetime import datetime`, `DateTime`, `Interval` from sqlalchemy, and `ARRAY`. Then append:

```python
class RawContent(Base):
    """One ingested social post or news article; scored asynchronously."""

    __tablename__ = "raw_content"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(256))
    url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    symbols: Mapped[list] = mapped_column(JSONB, default=list)
    engagement: Mapped[float | None] = mapped_column(Float)
    lang: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    sentiment_model: Mapped[str | None] = mapped_column(String(128))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_raw_content_source_external"),
        Index("ix_raw_content_unscored", "fetched_at",
              postgresql_where=text("scored_at IS NULL")),
    )


class ContentSentimentAgg(Base):
    """Per-symbol/window rollup derived from scored raw_content."""

    __tablename__ = "content_sentiment_agg"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    window_size: Mapped[int] = mapped_column(Integer, primary_key=True)
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    unique_authors: Mapped[int] = mapped_column(Integer, default=0)
    engagement_sum: Mapped[float] = mapped_column(Float, default=0.0)
    avg_sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Add the missing imports to the top-of-file import block: `from datetime import datetime` (already present), and extend the sqlalchemy import to include `DateTime`, and add `from sqlalchemy import text`. Add `func` import: `from sqlalchemy import func`.

Extend `HYPERTABLES`:

```python
HYPERTABLES = {
    "prices": "time",
    "sentiments": "time",
    "signals": "time",
    "raw_content": "fetched_at",
}
```

- [ ] **Step 3b: Run the model test**

Run: `python -m pytest tests/test_raw_content_model.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 3c: Write the migration**

Create `migrations/alembic/versions/0003_raw_content.py`:

```python
"""raw_content + content_sentiment_agg

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_content",
        sa.Column("id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("author", sa.String(256)),
        sa.Column("title", sa.Text),
        sa.Column("text", sa.Text, server_default=""),
        sa.Column("symbols", postgresql.JSONB, server_default="[]"),
        sa.Column("engagement", sa.Float),
        sa.Column("lang", sa.String(16)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("sentiment_score", sa.Float),
        sa.Column("sentiment_confidence", sa.Float),
        sa.Column("sentiment_model", sa.String(128)),
        sa.Column("scored_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", "fetched_at"),
        sa.UniqueConstraint("source", "external_id",
                            name="uq_raw_content_source_external"),
    )
    op.create_index(
        "ix_raw_content_unscored", "raw_content", ["fetched_at"],
        postgresql_where=sa.text("scored_at IS NULL"),
    )
    op.execute(
        "SELECT create_hypertable('raw_content', 'fetched_at', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )

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


def downgrade() -> None:
    op.drop_table("content_sentiment_agg")
    op.drop_table("raw_content")
```

> Timescale note: the hypertable time column (`fetched_at`) must be part of the primary key, hence the composite `PRIMARY KEY (id, fetched_at)`. The `(source, external_id)` unique constraint provides dedup.

- [ ] **Step 3d: Restore full `sources/__init__.py` exports** (from Task 3's target block) now that `loop`/`repository` will exist after Task 6. If Task 6 isn't done yet, restore only `repository` imports here and add `loop` in Task 6.

- [ ] **Step 4: Commit**

```bash
git add libs/cmi_common/cmi_common/db/models.py migrations/alembic/versions/0003_raw_content.py tests/test_raw_content_model.py
git commit -m "feat(db): add raw_content hypertable + content_sentiment_agg + migration"
```

---

### Task 6: `AdaptivePollLoop`

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/loop.py`
- Test: `tests/test_adaptive_poll_loop.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_adaptive_poll_loop.py`:

```python
"""AdaptivePollLoop: poll→persist; pause-until-reset on RateLimitedError; no failover."""

from __future__ import annotations

from cmi_common.sources import (
    AdaptivePollLoop, FakeContentRepository, RateLimitedError, RawItem,
)


class FakeCache:
    def __init__(self, allow: bool = True) -> None:
        self._allow = allow
        self.paused: dict[str, float] = {}

    async def allow(self, *_a) -> bool:
        return self._allow


class Sleeps:
    """Records sleeps and stops the loop after a fixed number of iterations."""

    def __init__(self, stop_after: int) -> None:
        self.calls: list[float] = []
        self._left = stop_after

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._left -= 1
        if self._left <= 0:
            raise StopAsyncIteration


class StubProvider:
    name = "stub"
    kind = "social"
    rate_limit = (60, 60)

    def __init__(self, *, items=None, raises=None) -> None:
        self._items = items or []
        self._raises = raises
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._items

    async def close(self) -> None:
        pass


async def _run(loop: AdaptivePollLoop) -> None:
    try:
        await loop.run()
    except StopAsyncIteration:
        pass


async def test_polls_and_persists_then_sleeps_interval() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(items=[RawItem(source="stub", kind="social", external_id="1")])
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert provider.calls == 1
    assert len(repo.rows) == 1
    assert sleeps.calls == [300]  # normal cadence


async def test_rate_limited_sleeps_retry_after_and_resumes_same_provider() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(raises=RateLimitedError(45.0))
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert sleeps.calls == [45.0]     # waited the API-provided reset, not the interval
    assert provider.calls == 1        # same provider — no failover to anything else


async def test_quota_guard_blocks_poll_and_waits_window() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(items=[RawItem(source="stub", kind="social", external_id="1")])
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(allow=False), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert provider.calls == 0        # never fetched — proactive budget spent
    assert sleeps.calls == [60]       # waited the rate-limit window
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_adaptive_poll_loop.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'AdaptivePollLoop'`

- [ ] **Step 3: Implement**

Create `libs/cmi_common/cmi_common/sources/loop.py`:

```python
"""AdaptivePollLoop — runs one provider forever with self-managed rate limits.

Each provider gets its own loop (fan-out; there is no failover). A loop polls,
persists via the repository, and sleeps its normal cadence. When the provider's
proactive budget is spent it waits the rate-limit window; when the provider
raises ``RateLimitedError`` it waits the API-derived ``retry_after`` (or the
provider window) and then resumes the SAME provider.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..cache import Cache
from ..observability import EVENTS_PRODUCED, UPSTREAM_REQUESTS
from .cascade import RateLimitedError
from .provider import Provider
from .repository import ContentRepository

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class AdaptivePollLoop:
    def __init__(
        self,
        provider: Provider,
        repository: ContentRepository,
        cache: Cache,
        *,
        poll_interval: float,
        service: str,
        error_backoff: float = 120.0,
        sleep: Sleep | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._cache = cache
        self._interval = poll_interval
        self._service = service
        self._error_backoff = error_backoff
        self._sleep = sleep or asyncio.sleep

    async def run(self) -> None:
        max_calls, window = self._provider.rate_limit
        name = self._provider.name
        while True:
            if not await self._cache.allow(name, max_calls, window):
                logger.debug("%s budget spent; waiting %ss", name, window)
                await self._sleep(window)
                continue
            try:
                items = await self._provider.fetch()
            except RateLimitedError as exc:
                wait = exc.retry_after if exc.retry_after is not None else window
                UPSTREAM_REQUESTS.labels(self._service, name, "ratelimit").inc()
                logger.info("%s rate-limited; pausing %ss", name, wait)
                await self._sleep(wait)
                continue
            except Exception:  # noqa: BLE001 - one bad poll never kills the loop
                UPSTREAM_REQUESTS.labels(self._service, name, "error").inc()
                logger.warning("%s poll failed; backing off", name, exc_info=True)
                await self._sleep(self._error_backoff)
                continue
            inserted = await self._repo.insert_items(items)
            UPSTREAM_REQUESTS.labels(self._service, name, "ok").inc()
            EVENTS_PRODUCED.labels(self._service, "raw_content", self._provider.kind).inc(
                inserted
            )
            logger.info("%s ingested %d new items", name, inserted)
            await self._sleep(self._interval)

    async def close(self) -> None:
        await self._provider.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_adaptive_poll_loop.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 5: Restore full `sources/__init__.py`** (the Task-3 target block, now that `loop`+`repository` exist). Run the whole framework suite:

Run: `python -m pytest tests/test_raw_item.py tests/test_parse_retry_after.py tests/test_circuit_breaker.py tests/test_content_repository.py tests/test_raw_content_model.py tests/test_adaptive_poll_loop.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/loop.py libs/cmi_common/cmi_common/sources/__init__.py tests/test_adaptive_poll_loop.py
git commit -m "feat(cmi_common): add AdaptivePollLoop with per-source rate-limit self-management"
```

---

## Phase B — Rewire existing sources to RawItem + fan-out DB

### Task 7: Bluesky + Reddit providers return `RawItem`

**Files:**
- Modify: `services/collector-social/app/providers/bluesky.py`
- Modify: `services/collector-social/app/providers/reddit.py`
- Modify: `tests/test_bluesky_provider.py`, `tests/test_reddit_provider.py`

- [ ] **Step 1: Update the Bluesky test to expect RawItem**

Replace the body-shape assertions in `tests/test_bluesky_provider.py`'s `test_aggregates_cashtags` with per-item expectations. Change the assertions to:

```python
    items = await provider.fetch()
    await provider.close()

    assert {i.source for i in items} == {"bluesky"}
    assert all(i.kind == "social" for i in items)
    btc = [i for i in items if "BTC" in i.symbols]
    assert len(btc) == 2  # two posts mention $BTC
    assert btc[0].external_id  # a stable post id
    assert "$BTC" in btc[0].text
```

Update `_post` to include a stable id (`"uri"`), e.g. add `"uri": f"at://{did}/{likes}"` inside `_post`. Keep the 429 → `RateLimitedError` test (rename import to `RateLimitedError`).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_bluesky_provider.py -q -p no:cacheprovider`
Expected: FAIL (provider still returns aggregated `SocialEvent`)

- [ ] **Step 3: Rewrite `bluesky.py` to yield per-post `RawItem`**

Replace `services/collector-social/app/providers/bluesky.py` with:

```python
"""Bluesky provider -> one RawItem per crypto post."""

from __future__ import annotations

import re

import httpx

from cmi_common.sources import Provider, RateLimitedError, RawItem, parse_retry_after
from cmi_common.sources.provider import Provider as _P  # noqa: F401 - protocol hint

SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class BlueskyProvider:
    name = "bluesky"
    kind = "social"
    rate_limit = (250, 300)  # ~3000/5min shared; stay well under

    def __init__(self, *, query: str = "crypto", limit: int = 100,
                 user_agent: str = "cmi-collector/0.1") -> None:
        self._query = query
        self._limit = limit
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.get(
                SEARCH_URL, params={"q": self._query, "limit": self._limit}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=300)) from exc
            raise
        posts = resp.json().get("posts", [])
        items: list[RawItem] = []
        for post in posts:
            text = post.get("record", {}).get("text", "")
            symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
            if not symbols:
                continue
            engagement = float(
                post.get("likeCount", 0) + post.get("repostCount", 0)
                + post.get("replyCount", 0)
            )
            items.append(RawItem(
                source="bluesky", kind="social",
                external_id=str(post.get("uri") or post.get("cid")),
                text=text, author=post.get("author", {}).get("did"),
                symbols=symbols, engagement=engagement,
            ))
        return items
```

- [ ] **Step 4: Run Bluesky test**

Run: `python -m pytest tests/test_bluesky_provider.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Update the Reddit test + rewrite `reddit.py` the same way**

Update `tests/test_reddit_provider.py`: assert per-post `RawItem` (source `reddit`, kind `social`, `external_id` from the post `name`/`id`, `text` from title). Keep the quota-exhausted → `RateLimitedError` test (import `RateLimitedError`).

Rewrite `services/collector-social/app/providers/reddit.py` to yield one `RawItem` per post that mentions a cashtag:

```python
"""Reddit provider -> one RawItem per crypto post."""

from __future__ import annotations

import re
from typing import Any

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class RedditProvider:
    name = "reddit"
    kind = "social"
    rate_limit = (60, 60)

    def __init__(self, *, subreddits: list[str] | None = None,
                 client_id: str | None = None, client_secret: str | None = None,
                 user_agent: str = "cmi-collector/0.1") -> None:
        self._subreddits = subreddits or ["CryptoCurrency", "CryptoMoonShots", "solana"]
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=15.0)
        self._token: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_sub(self, sub: str) -> list[dict[str, Any]]:
        url = f"https://www.reddit.com/r/{sub}/new.json"
        try:
            resp = await self._client.get(url, params={"limit": 100})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
            raise
        return [c["data"] for c in resp.json().get("data", {}).get("children", [])]

    async def fetch(self) -> list[RawItem]:
        items: list[RawItem] = []
        for sub in self._subreddits:
            for post in await self._fetch_sub(sub):
                title = post.get("title", "")
                body = post.get("selftext", "")
                symbols = sorted({m.upper() for m in _CASHTAG.findall(f"{title} {body}")})
                if not symbols:
                    continue
                items.append(RawItem(
                    source="reddit", kind="social",
                    external_id=str(post.get("name") or post.get("id")),
                    title=title, text=f"{title} {body}".strip(),
                    author=post.get("author"), symbols=symbols,
                    engagement=float(post.get("score", 0) + post.get("num_comments", 0)),
                ))
        return items
```

- [ ] **Step 6: Run both provider tests**

Run: `python -m pytest tests/test_bluesky_provider.py tests/test_reddit_provider.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/collector-social/app/providers tests/test_bluesky_provider.py tests/test_reddit_provider.py
git commit -m "refactor(collector-social): providers yield per-post RawItem"
```

---

### Task 8: CryptoCompare + RSS providers return `RawItem`

**Files:**
- Modify: `services/collector-news/app/providers/cryptocompare.py`, `rss.py`
- Modify: `tests/test_cryptocompare_news_provider.py`, `tests/test_rss_provider.py`

- [ ] **Step 1: Update tests to expect `RawItem`** (news `kind`, `external_id`, `title`/`text`/`url`/`symbols`). Keep CryptoCompare quota→`RateLimitedError`; keep RSS malformed-item-skipped and stable-hash tests but assert the returned objects are `RawItem` with `source="rss"`.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cryptocompare_news_provider.py tests/test_rss_provider.py -q -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: Rewrite `cryptocompare.py`**

```python
"""CryptoCompare news provider -> one RawItem per article."""

from __future__ import annotations

from typing import Any

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after


class CryptoCompareNewsProvider:
    name = "cryptocompare"
    kind = "news"
    rate_limit = (2, 60)  # ~86k/month under the 100k free cap

    def __init__(self, base_url: str, api_key: str | None) -> None:
        headers = {"authorization": f"Apikey {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.get(
                "/data/v2/news/", params={"lang": "EN", "sortOrder": "latest"}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
            raise
        return [self._to_item(a) for a in resp.json().get("Data", [])]

    def _to_item(self, art: dict[str, Any]) -> RawItem:
        categories = [c for c in str(art.get("categories", "")).split("|") if c]
        symbols = [c.upper() for c in categories if c.isupper() and len(c) <= 6]
        return RawItem(
            source="cryptocompare", kind="news", external_id=str(art.get("id")),
            title=art.get("title", ""), text=art.get("body", "")[:4000],
            url=art.get("url"), symbols=symbols,
        )
```

> Note: the proactive rate-limit budget now lives in `AdaptivePollLoop` (via `provider.rate_limit` + `cache.allow`), so the provider no longer calls `cache.allow` itself. The incremental cursor is dropped — dedup is handled by the DB unique constraint on `(source, external_id)`.

- [ ] **Step 4: Rewrite `rss.py`** to yield `RawItem` (keep stable `hashlib.sha1` feed key and per-item try/except-skip from the prior fixes):

```python
"""RSS news provider -> one RawItem per article (keyless floor)."""

from __future__ import annotations

import hashlib
import logging
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from cmi_common.sources import RawItem

logger = logging.getLogger(__name__)
DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]


class RSSProvider:
    name = "rss"
    kind = "news"
    rate_limit = (600, 60)  # effectively unlimited; loop cadence bounds it

    def __init__(self, *, feeds: list[str] | None = None,
                 user_agent: str = "cmi-collector/0.1") -> None:
        self._feeds = feeds or DEFAULT_FEEDS
        self._client = httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        items: list[RawItem] = []
        for feed in self._feeds:
            try:
                resp = await self._client.get(feed)
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.warning("RSS feed unreachable: %s", feed)
                continue
            items.extend(self._parse(feed, resp.text))
        return items

    def _parse(self, feed: str, body: str) -> list[RawItem]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            logger.warning("failed to parse RSS feed %s", feed)
            return []
        src = hashlib.sha1(feed.encode()).hexdigest()[:16]
        out: list[RawItem] = []
        for node in root.iterfind(".//item"):
            guid = _text(node, "guid") or _text(node, "link")
            link = _text(node, "link")
            if not guid or not link:
                continue
            try:
                out.append(RawItem(
                    source="rss", kind="news",
                    external_id=f"{src}:{guid}",
                    title=_text(node, "title") or "",
                    text=(_text(node, "description") or "")[:4000],
                    url=link, published_at=_dt(_text(node, "pubDate")),
                ))
            except Exception:  # noqa: BLE001 - a bad item never sinks the feed
                logger.warning("skipping malformed RSS item %s in %s", guid, feed)
        return out


def _text(node: ElementTree.Element, tag: str) -> str | None:
    el = node.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _dt(pubdate: str | None):
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 5: Run both news provider tests**

Run: `python -m pytest tests/test_cryptocompare_news_provider.py tests/test_rss_provider.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/collector-news/app/providers tests/test_cryptocompare_news_provider.py tests/test_rss_provider.py
git commit -m "refactor(collector-news): providers yield per-article RawItem"
```

---

### Task 9: Fan-out wiring in both collector services

**Files:**
- Modify: `services/collector-social/app/main.py`, `services/collector-news/app/main.py`
- Modify: both `pyproject.toml` (add `sqlalchemy>=2.0`, `asyncpg>=0.29`)

- [ ] **Step 1: Rewrite `collector-social/app/main.py`** to run one `AdaptivePollLoop` per provider, each persisting to the DB via a per-tick session:

```python
"""collector-social: fan-out AdaptivePollLoop per social provider -> raw_content."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.sources import AdaptivePollLoop, SqlContentRepository

from .providers.bluesky import BlueskyProvider
from .providers.reddit import RedditProvider

POLL_INTERVAL = float(os.getenv("SOCIAL_POLL_INTERVAL", "300"))
SUBREDDITS = os.getenv("REDDIT_SUBREDDITS",
                       "CryptoCurrency,CryptoMoonShots,solana").split(",")


def _build_providers() -> list:
    providers = [BlueskyProvider(query=os.getenv("BLUESKY_QUERY", "crypto"))]
    providers.append(RedditProvider(
        subreddits=SUBREDDITS,
        client_id=os.getenv("REDDIT_CLIENT_ID") or None,
        client_secret=os.getenv("REDDIT_CLIENT_SECRET") or None,
    ))
    return providers


class _RepoFactory:
    """Yields a fresh SqlContentRepository bound to a new session per insert."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_items(self, items) -> int:
        async with self._db._sessionmaker() as session:  # noqa: SLF001
            return await SqlContentRepository(session).insert_items(items)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    repo = _RepoFactory(db)
    providers = _build_providers()
    loops = [
        AdaptivePollLoop(p, repo, cache, poll_interval=POLL_INTERVAL,
                         service="collector-social")
        for p in providers
    ]
    app.state.cache = cache
    app.state.db = db
    app.state.loops = loops
    app.state.tasks = [asyncio.create_task(loop.run()) for loop in loops]


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    for loop in app.state.loops:
        await loop.close()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-social", on_startup=_startup, on_shutdown=_shutdown)
```

> The `_RepoFactory` exposes `insert_items` only (all `AdaptivePollLoop` needs); it opens a short-lived session per poll so long-lived loops don't hold connections. Expose `_sessionmaker` on `Database` as a public `sessionmaker` property in a one-line change to `db/session.py` to avoid the private access:
> add to `Database`: `@property\n    def sessionmaker(self):\n        return self._sessionmaker` and use `self._db.sessionmaker()` above.

- [ ] **Step 2: Rewrite `collector-news/app/main.py`** analogously with `CryptoCompareNewsProvider` + `RSSProvider`:

```python
"""collector-news: fan-out AdaptivePollLoop per news provider -> raw_content."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.sources import AdaptivePollLoop, SqlContentRepository

from .providers.cryptocompare import CryptoCompareNewsProvider
from .providers.rss import RSSProvider

POLL_INTERVAL = float(os.getenv("NEWS_POLL_INTERVAL", "300"))
CC_BASE_URL = os.getenv("CRYPTOCOMPARE_BASE_URL", "https://min-api.cryptocompare.com")
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or None
RSS_FEEDS = [f for f in os.getenv("RSS_FEEDS", "").split(",") if f]


class _RepoFactory:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_items(self, items) -> int:
        async with self._db.sessionmaker() as session:
            return await SqlContentRepository(session).insert_items(items)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    repo = _RepoFactory(db)
    providers = [
        CryptoCompareNewsProvider(CC_BASE_URL, CC_API_KEY),
        RSSProvider(feeds=RSS_FEEDS or None),
    ]
    loops = [
        AdaptivePollLoop(p, repo, cache, poll_interval=POLL_INTERVAL,
                         service="collector-news")
        for p in providers
    ]
    app.state.cache = cache
    app.state.db = db
    app.state.loops = loops
    app.state.tasks = [asyncio.create_task(loop.run()) for loop in loops]


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    for loop in app.state.loops:
        await loop.close()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-news", on_startup=_startup, on_shutdown=_shutdown)
```

- [ ] **Step 3: Add the `sessionmaker` property to `db/session.py`**

In `libs/cmi_common/cmi_common/db/session.py`, add to `Database`:

```python
    @property
    def sessionmaker(self):
        return self._sessionmaker
```

- [ ] **Step 4: Add DB deps to both services' `pyproject.toml`**

Set `dependencies` in `services/collector-social/pyproject.toml` and `services/collector-news/pyproject.toml` to:

```toml
dependencies = ["cmi-common", "httpx>=0.27", "sqlalchemy>=2.0", "asyncpg>=0.29"]
```

- [ ] **Step 5: Import-smoke both services + run provider tests**

Run: `python -m pytest tests/test_bluesky_provider.py tests/test_reddit_provider.py tests/test_cryptocompare_news_provider.py tests/test_rss_provider.py -q -p no:cacheprovider`
Expected: PASS. Then byte-compile the mains:
`python -m py_compile services/collector-social/app/main.py services/collector-news/app/main.py`
Expected: no output (success).

- [ ] **Step 6: Commit**

```bash
git add services/collector-social/app/main.py services/collector-news/app/main.py services/collector-social/pyproject.toml services/collector-news/pyproject.toml libs/cmi_common/cmi_common/db/session.py
git commit -m "feat(collectors): fan-out adaptive poll loops persisting to raw_content"
```

---

## Phase C — DB-sourced sentiment worker

### Task 10: `SentimentDbWorker`

**Files:**
- Create: `services/sentiment-service/app/worker.py`
- Test: `tests/test_sentiment_worker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sentiment_worker.py`:

```python
"""SentimentDbWorker: score unscored rows, aggregate, publish SentimentEvent."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cmi_common.sources import FakeContentRepository, RawItem

_spec = importlib.util.spec_from_file_location(
    "sworker",
    Path(__file__).resolve().parents[1]
    / "services" / "sentiment-service" / "app" / "worker.py",
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
    await repo.insert_items([
        RawItem(source="bluesky", kind="social", external_id="1",
                text="$BTC up", symbols=["BTC"], engagement=5.0),
        RawItem(source="rss", kind="news", external_id="2",
                title="ETH", text="steady", symbols=["ETH"]),
    ])
    producer = FakeProducer()
    worker = sw.SentimentDbWorker(repo, FakeScorer(), producer, batch=10)

    processed = await worker.run_once()

    assert processed == 2
    assert len(await repo.fetch_unscored(10)) == 0     # all marked scored
    symbols = {e.symbol for e in producer.published}
    assert symbols == {"BTC", "ETH"}                    # one SentimentEvent per symbol
    assert repo.aggregates                              # aggregate rows upserted


async def test_symbolless_item_scored_as_market() -> None:
    repo = FakeContentRepository()
    await repo.insert_items([
        RawItem(source="gdelt", kind="news", external_id="9", title="t", text="up"),
    ])
    producer = FakeProducer()
    worker = sw.SentimentDbWorker(repo, FakeScorer(), producer, batch=10)

    await worker.run_once()

    assert {e.symbol for e in producer.published} == {"MARKET"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_sentiment_worker.py -q -p no:cacheprovider`
Expected: FAIL — worker module missing

- [ ] **Step 3: Implement**

Create `services/sentiment-service/app/worker.py`:

```python
"""DB-sourced sentiment worker.

Polls ``raw_content`` for unscored rows, scores each with the HF scorer, writes
the score back, upserts the per-symbol/window aggregate, and publishes one
``SentimentEvent`` per (item x detected symbol) so downstream keeps receiving
per-symbol sentiment. Replaces the former Kafka news/social consumer.
"""

from __future__ import annotations

import logging
from datetime import timedelta, timezone

from cmi_common.events.sentiment import SentimentEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED
from cmi_common.sources import ContentRepository

logger = logging.getLogger(__name__)
SERVICE = "sentiment-service"
WINDOW_SIZE = 3600  # seconds


class SentimentDbWorker:
    def __init__(self, repository: ContentRepository, scorer, producer: EventProducer,
                 *, batch: int = 100) -> None:
        self._repo = repository
        self._scorer = scorer
        self._producer = producer
        self._batch = batch

    async def run_once(self) -> int:
        rows = await self._repo.fetch_unscored(self._batch)
        for row in rows:
            text = f"{row.title}. {row.text}" if row.title else row.text
            result = self._scorer.score(text)
            EVENTS_CONSUMED.labels(SERVICE, "raw_content", row.kind).inc()
            await self._repo.mark_scored(
                row.id, score=result.score, confidence=result.confidence,
                model=result.model_name,
            )
            symbols = row.symbols or ["MARKET"]
            window_start = _floor_hour(row.published_at)
            for symbol in symbols:
                await self._repo.upsert_aggregate(
                    symbol=symbol, kind=row.kind, window_start=window_start,
                    window_size=WINDOW_SIZE, mentions=1,
                    unique_authors=1, engagement_sum=float(row.engagement or 0.0),
                    avg_sentiment=result.score,
                    weighted_sentiment=result.score * result.confidence,
                )
                await self._producer.publish(Topic.SENTIMENT, SentimentEvent(
                    symbol=symbol, sentiment_score=result.score,
                    confidence=result.confidence, model_name=result.model_name,
                    input_kind=row.kind, sample_size=1,
                    meta={"source": row.source},
                ))
                EVENTS_PRODUCED.labels(SERVICE, Topic.SENTIMENT.value, "SentimentEvent").inc()
        logger.info("sentiment worker scored %d rows", len(rows))
        return len(rows)


def _floor_hour(dt):
    from datetime import datetime

    base = dt if dt is not None else datetime.now(tz=timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.replace(minute=0, second=0, microsecond=0)
```

> Confirm `SentimentEvent` fields against `libs/cmi_common/cmi_common/events/sentiment.py` before implementing; the kwargs above mirror the current `handler.py` usage (`symbol`, `sentiment_score`, `confidence`, `model_name`, `input_kind`, `sample_size`, `meta`). `_floor_hour` and `timedelta` import are used for the window bucket.

- [ ] **Step 4: Run worker test**

Run: `python -m pytest tests/test_sentiment_worker.py -q -p no:cacheprovider`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/sentiment-service/app/worker.py tests/test_sentiment_worker.py
git commit -m "feat(sentiment-service): add DB-sourced scoring worker"
```

---

### Task 11: Wire the worker into `sentiment-service`, drop the Kafka consumer

**Files:**
- Modify: `services/sentiment-service/app/main.py`
- Modify: `services/sentiment-service/pyproject.toml` (add `sqlalchemy>=2.0`, `asyncpg>=0.29`)
- Delete: `services/sentiment-service/app/handler.py` (replaced by the worker)
- Delete/keep: `tests/test_sentiment.py` — inspect; if it tests `SentimentHandler`, replace its assertions with worker-based ones or delete if fully covered by `test_sentiment_worker.py`.

- [ ] **Step 1: Rewrite `sentiment-service/app/main.py`**

```python
"""sentiment-service: DB-sourced scoring worker (publishes SentimentEvent)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.db.session import Database
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic
from cmi_common.sources import SqlContentRepository

from .scorer import SentimentScorer
from .worker import SentimentDbWorker

MODEL_NAME = os.getenv("SENTIMENT_MODEL", "ElKulako/cryptobert")
WORKER_INTERVAL = float(os.getenv("SENTIMENT_WORKER_INTERVAL", "10"))
BATCH = int(os.getenv("SENTIMENT_BATCH", "100"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    producer = EventProducer(settings.kafka)
    await producer.start()
    db = Database(settings.db)
    scorer = SentimentScorer(MODEL_NAME)

    async def _tick() -> None:
        async with db.sessionmaker() as session:
            worker = SentimentDbWorker(
                SqlContentRepository(session), scorer, producer, batch=BATCH
            )
            await worker.run_once()

    app.state.producer = producer
    app.state.db = db
    app.state.worker_task = asyncio.create_task(
        run_periodic(_tick, WORKER_INTERVAL, name="sentiment-worker")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.worker_task.cancel()
    await asyncio.gather(app.state.worker_task, return_exceptions=True)
    await app.state.db.dispose()
    await app.state.producer.stop()


app = create_app("sentiment-service", on_startup=_startup, on_shutdown=_shutdown)
```

- [ ] **Step 2: Delete the old handler + update deps**

```bash
git rm services/sentiment-service/app/handler.py
```
Set `services/sentiment-service/pyproject.toml` dependencies to include `sqlalchemy>=2.0` and `asyncpg>=0.29` alongside the existing ones.

Inspect `tests/test_sentiment.py`: if it imports `SentimentHandler`, delete it (the worker test covers scoring→publish) — `git rm tests/test_sentiment.py`. If it tests the pure `SentimentScorer`, keep it.

- [ ] **Step 3: Verify**

Run: `python -m pytest tests/test_sentiment_worker.py tests/test_scoring.py -q -p no:cacheprovider` (and `test_sentiment.py` if kept).
`python -m py_compile services/sentiment-service/app/main.py`
Expected: PASS / no output.

- [ ] **Step 4: Commit**

```bash
git add services/sentiment-service tests/test_sentiment.py
git commit -m "feat(sentiment-service): run DB worker, retire Kafka news/social consumer"
```

---

## Phase D — Ops & full verification

### Task 12: Compose env, `.env.example`, migration, full suite + docs

**Files:**
- Modify: `docker-compose.yml` (collector env: DB already via `common-env`? verify; add `SENTIMENT_WORKER_INTERVAL`, `SENTIMENT_BATCH` to sentiment-service)
- Modify: `.env.example`
- Modify: `CLAUDE.md` pipeline paragraph, `memory/` (repo memory file)

- [ ] **Step 1: Confirm services get DB env**

Check `docker-compose.yml`'s `*common-env` anchor includes `DB_*` (the collectors previously didn't need DB). If `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME` are in `common-env`, collectors inherit them. If not, add them to `common-env` so `collector-social`/`collector-news`/`sentiment-service` can reach Postgres. Add `depends_on: [postgres]` to both collectors.

- [ ] **Step 2: Add sentiment worker knobs + collector poll knobs to `.env.example`**

Append under the intervals section:

```
SENTIMENT_WORKER_INTERVAL=10
SENTIMENT_BATCH=100
```

- [ ] **Step 3: Run the migration against a live DB (if available) or validate it imports**

If a dev Postgres is up: `make migrate` and confirm `raw_content` + `content_sentiment_agg` exist and `raw_content` is a hypertable (`SELECT * FROM timescaledb_information.hypertables;`). If no DB is available in this environment, at minimum: `python -c "import importlib.util,pathlib; importlib.util.spec_from_file_location('m', pathlib.Path('migrations/alembic/versions/0003_raw_content.py'))"` and eyeball the SQL. Note in the commit which was done.

- [ ] **Step 4: Full suite + lint on feature files**

Run: `python -m pytest -q -p no:cacheprovider` — expect all green (framework + provider + worker tests; cascade test gone).
Run: `python -m ruff check libs/cmi_common/cmi_common/sources services/collector-social services/collector-news services/sentiment-service` and `ruff format --check` on the same — expect clean. Fix any E501/format in feature files.

- [ ] **Step 5: Update `CLAUDE.md` pipeline paragraph**

Replace the cascade paragraph (added in the prior feature) with:

```
Collectors (coingecko, dexscreener) are stateless producers. Social + news ingestion runs
as two fan-out services — `collector-social` (Bluesky, Reddit, …) and `collector-news`
(CryptoCompare, RSS, …) — where each platform runs its own adaptive poll loop that
self-throttles on its rate limit (learned from the API's headers) and persists raw items to
Postgres `raw_content`. `sentiment-service` scores unscored rows from the DB, upserts
`content_sentiment_agg`, and still publishes `SentimentEvent` on Kafka for decision-engine.
```

- [ ] **Step 6: Add a repo memory file** `memory/db-sourced-ingestion.md` following the existing `memory/*.md` convention, summarizing: fan-out (no failover), `raw_content`/`content_sentiment_agg`, `AdaptivePollLoop` self-throttle, sentiment reads DB + publishes Kafka. Add its index line to `memory/MEMORY.md`.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .env.example CLAUDE.md memory
git commit -m "feat: DB-sourced fan-out ingestion — compose/env/docs"
```

---

## Self-Review

**Spec coverage:**
- DB-as-source flow → Tasks 5 (schema), 9 (collectors write), 10–11 (sentiment reads DB). ✓
- Fan-out not failover → Task 6 (`AdaptivePollLoop`, one per provider), 9 (per-provider tasks). ✓
- Rate-limit self-management from API headers → Task 2 (`parse_retry_after`), 6 (loop pause-until-reset). ✓
- Persist raw + aggregate → Task 5 (both tables), 10 (`upsert_aggregate`). ✓
- Keep Kafka `SentimentEvent` downstream → Task 10 publishes. ✓
- Retire `SourceCascade` → Task 3. ✓
- Existing 4 sources migrated → Tasks 7–8. Plan 2 adds the rest (out of scope here, by decomposition).

**Placeholder scan:** Each code/test step has full content. Two explicit ordering dependencies are called out (Task 4 needs Task 5's models; Task 3/5/6 stage the `__init__` exports) — these are sequencing notes, not placeholders.

**Type consistency:** `Provider` (name/kind/rate_limit/fetch→`list[RawItem]`/close) is used identically in providers (Tasks 7–8) and the loop (Task 6). `ContentRepository` methods (`insert_items`, `fetch_unscored`→`UnscoredRow`, `mark_scored`, `upsert_aggregate`) match across `SqlContentRepository`, `FakeContentRepository`, the loop, and the worker. `RateLimitedError` (renamed from `RateLimited`) is raised by all providers and caught by the loop. `raw_item_to_row` output keys match `RawContent` columns and the migration.

**Known follow-ups (Plan 2):** add the ~10 new providers (Mastodon, 4chan, Google News RSS, GDELT, Coinpaprika, Farcaster, StockTwits, Lens, YouTube, CoinGecko news, Messari, NewsData, Telegram), each as a `Provider` returning `RawItem` inserted into the fan-out lists in Task 9's `_build_providers`, plus their env/no-op guards and Prometheus/doc updates.
