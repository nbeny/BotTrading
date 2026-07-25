# Sentiment Aggregation Rework — Design

**Date:** 2026-07-25
**Status:** Approved (design), pending implementation plan
**Services touched:** `sentiment-service`, `api-gateway`, `libs/cmi_common`, `migrations`, `frontend` (types)

## Problem

The sentiment pipeline scores each `raw_content` row and maintains a rollup table
`content_sentiment_agg`, but that rollup has several issues:

1. **The rollup is dead.** `content_sentiment_agg` is written by the worker but read by
   nobody. The api-gateway recomputes everything on the fly from `raw_content` on every
   request (`services/api-gateway/app/read_api.py:185-226`), rebucketing scores into 12
   hourly buckets per call. This does not scale and duplicates logic.
2. **Only one window (1h).** The rollup key is `(symbol, kind, window_start, window_size)`
   with `window_size` hard-coded to 3600s. There is no way to compare "last hour" vs
   "last 24h" vs "last week" — the accelerations/reversals that make an actionable signal.
3. **`unique_authors` is wrong.** It adds `+1` per contribution rather than counting
   distinct authors (the code comment admits this). Useless for organic-buzz vs spam.
4. **Stored means are not additive.** `avg_sentiment`/`weighted_sentiment` are stored as a
   running mention-weighted mean. This cannot be merged across buckets to derive a longer
   window, and accumulates float drift.
5. **Binary HF score.** `_score_hf` (`scorer.py:70-83`) takes the top label and signs it
   `±1 × score`. A "slightly bullish" and an "ultra bullish" collapse to the same value.
6. **No temporal decay.** An item 55 minutes old weighs the same as one 5 minutes old.

## Goals

- Make `content_sentiment_agg` the **source of truth** for the read plane.
- Expose sentiment per crypto across **arbitrary trailing windows**: 1h, 24h, 7d, 30d,
  6 months, 1 year, 5 years — from a single stored resolution, without write amplification.
- Fix distinct-author counting (honestly, given no `postgres-hll`).
- Continuous HF sentiment score.
- Optional exponential time decay at read time.

## Chosen approach: hourly base buckets + windows derived at read time

Instead of materializing one row per rolling window (8× write amplification, and multi-year
rolling windows are meaningless to pre-store), materialize **fixed hourly calendar buckets**
per `(symbol, kind)` and **derive any trailing window by range aggregation** at read time.

A single resolution (hourly) is used. 5 years of hourly buckets is ~43,800 rows per
`(symbol, kind)` series — trivial to aggregate for the single-symbol reads the dashboard
does, given the `(symbol, kind, bucket_start)` primary key. A coarser `day` rollup tier is a
deliberate future optimization (see Out of scope), not needed for v1: it adds a compaction
loop and a read-time boundary union (recent hourly buckets not yet rolled up) for scale we
do not have yet.

The DB image is `timescale/timescaledb:2.15.3-pg16`, which does **not** bundle
`postgres-hll`, so mergeable HyperLogLog distinct-author sketches are not available. This
constrains the distinct-author design (see Data model).

## Data model

`content_sentiment_agg` is replaced (no readers today, data is recomputable → drop/create,
not a data migration). All stored quantities are **additive** so windows merge by summing;
means are computed once at read time.

| Column | Type | Role |
|---|---|---|
| `symbol` | `String(32)`, PK | crypto ticker or `MARKET` |
| `kind` | `String(16)`, PK | `news`, `social`, … |
| `bucket_start` | `timestamptz`, PK | hourly calendar bucket start (floored to the hour) |
| `mentions` | `Integer` | Σ items → `avg = score_sum / mentions` |
| `score_sum` | `Float` | Σ score |
| `confidence_sum` | `Float` | Σ confidence (weight) |
| `weighted_score_sum` | `Float` | Σ (score · confidence) → `weighted_avg = weighted_score_sum / confidence_sum` |
| `engagement_sum` | `Float` | Σ engagement |
| `updated_at` | `timestamptz` | last write |

`resolution`/`window_size` columns are gone. The primary key is
`(symbol, kind, bucket_start)`, which directly serves range queries.

**Distinct authors.** With no `postgres-hll`, an additive distinct count is impossible.
Decision (YAGNI — nobody reads this column today): drop the misleading `unique_authors`
from the bucket entirely. Expose distinct authors **at read time** via
`COUNT(DISTINCT author)` on `raw_content`, restricted to short windows (≤ 30d, bounded
scan). Long windows do not report distinct authors. This keeps the aggregate table 100%
additive and honest.

## Scoring worker (`sentiment-service`)

`app/worker.py`, modified:
- No `UnscoredRow` change needed: the worker already has `result.score`/`result.confidence`
  from the scorer and `row.engagement` from the row. Distinct authors are read from
  `raw_content` at read time, not stored per bucket, so `author` is not needed here.
- Per scored item, upsert the hourly bucket additively — one upsert per (symbol, kind):
  `mentions += 1`, `score_sum += score`, `confidence_sum += confidence`,
  `weighted_score_sum += score·confidence`, `engagement_sum += engagement`. No write
  amplification (one bucket per item×symbol, as today).
- `SentimentEvent` on Kafka is unchanged (decision-engine unaffected).

**Delivery semantics.** Unchanged: a row is marked `scored` before its `SentimentEvent` is
published. `mark_scored` remains the anti-replay guard (an additive upsert makes double
scoring visible as double counting, so the guard matters).

No compaction loop in v1 (single resolution).

## Repository (`libs/cmi_common/sources/repository.py`)

`upsert_aggregate` becomes purely additive:
`ON CONFLICT (symbol, kind, bucket_start) DO UPDATE SET col = table.col + excluded.col`.
Signature changes to the additive columns (`score_sum`, `confidence_sum`,
`weighted_score_sum`, `engagement_sum`, `mentions`). Simpler and exact vs the current
running-mean. `window_start`/`window_size` params are replaced by `bucket_start`.

## Scorer (`app/scorer.py`) — continuous score

`_score_hf` computes a continuous score as the expectation over label probabilities.
CryptoBERT returns `{Bullish, Neutral, Bearish}` with `top_k=None` (already set):

- `score = P(bull) − P(bear)` → continuous in `[-1, 1]`.
- `confidence = 1 − P(neutral)` → engagement away from neutral.

Label detection reuses the existing `pos`/`bull` and `neg`/`bear` matching. The lexical
fallback (`_score_lexicon`) is already continuous and is left unchanged. The scorer stays
time-agnostic (decay lives in the read layer).

## Read plane (`api-gateway` + shared reader)

A new `SentimentAggReader` in `cmi_common/sources` (alongside the write repository) carries
three queries; the api-gateway exposes them read-only.

**Window derivation (core).** For window `W`: sum additive columns over hourly buckets with
`bucket_start ≥ now − W`, then divide once:
`avg = Σ score_sum / Σ mentions`; `weighted_avg = Σ weighted_score_sum / Σ confidence_sum`;
`mentions`, `engagement` = direct sums. Empty window → zeros.

**Time decay (opt-in).** Query param `decay=<half-life hours>`. Each bucket is weighted by
`exp(−age_h / half_life)` in SQL (`exp()` native). `weighted_avg = Σ(weighted_score_sum·d_b)
/ Σ(confidence_sum·d_b)`. Without the param all weights = 1 (plain aggregate, default — no
surprise for the dashboard).

**Endpoints** (`/api/v1/sentiment`, read-only):

| Endpoint | Role |
|---|---|
| `GET /windows?symbol=BTC&kind=all&decay=` | Full window set {1h,24h,7d,30d,6mo,1y,5y} with avg/weighted/mentions/engagement per crypto |
| `GET /series?symbol=BTC&points=12` | Hourly bucket time series for charts (last N hours) |
| `GET /authors?symbol=BTC&window=7d` | `COUNT(DISTINCT author)` on `raw_content`, windows ≤ 30d only |

The window set is a server-side constant `{1h, 24h, 7d, 30d, 6mo, 1y, 5y}` (matching the
approved set — 10y/20y dropped, 6mo added).

**Repointing.** The current `sentiment_series`/`avg_sentiment` computation
(`read_api.py:185-226`, which scans `raw_content`) is replaced by calls to `series`/`windows`.
Dashboard requests no longer scan `raw_content`.

## Migration, tests, backward compatibility

**Alembic `0004`** — reshape `content_sentiment_agg`:
- Drop/create with the new additive schema, PK `(symbol, kind, bucket_start)` (no readers,
  recomputable data). `downgrade()` restores the old schema.
- Add a GIN index on `raw_content.symbols` and an index on `published_at` for the `authors`
  query (`symbols @> '["BTC"]'`).
- `HYPERTABLES` unchanged (aggregate stays a plain composite-key table).

**Backfill** — table starts empty. Optional one-shot script replays scored `raw_content`
into hourly buckets (reuses `_floor_hour` + additive upsert), **disabled by default** (flag).

**Tests** (mirroring the existing `Fake*`/`Sql*` split):
- *Scorer*: continuous score — `{bull:.7, neu:.2, bear:.1}` → `score≈0.6`, `conf≈0.8`;
  neutral case; lexical fallback unchanged.
- *Worker*: additive upsert via `FakeContentRepository` — 3 items same bucket → correct
  sums; multi-symbol; `MARKET` default.
- *Reader* (Postgres integration): 24h vs 7d derivation over seeded buckets; decay (short
  half-life weights recent buckets more); empty window → zeros; `authors` distinct count.
- *api-gateway*: `/windows`, `/series`, `/authors` response shapes + `sentiment_series`
  repointing.

**Backward compatibility**: `SentimentEvent` Kafka schema **unchanged** (decision-engine
unaffected). Internal contract change: `upsert_aggregate` signature (additive columns +
`bucket_start`). `UnscoredRow` is unchanged. Frontend `content.ts` types
`content_sentiment_agg` — realign to the new schema in the same batch.

## Out of scope

- **Two-tier `day` rollup + compaction loop.** A coarser daily resolution for cheaper
  multi-symbol long-window scans. Deferred: it needs a compaction loop plus a read-time
  boundary union (recent hourly buckets not yet rolled into `day`), for scale not present
  yet. Hourly-only is correct and fast enough for single-symbol reads.
- TimescaleDB continuous aggregates (would require making sentiment a hypertable / changing
  `raw_content` dedup — considered and rejected).
- Mergeable HyperLogLog distinct authors (no `postgres-hll` in the image).
- Distinct authors over windows > 30d.
