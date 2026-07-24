# Multi-Platform Ingestion → DB → Sentiment — Design

**Status:** Approved (design), pending implementation plan
**Date:** 2026-07-24
**Supersedes (partially):** the cascade failover model from
`2026-07-22-resilient-sentiment-source-cascade.md` — `SourceCascade` is retired; the
`Provider` / `RateLimitedError` / rate-limit primitives are kept and evolved.

## Goal

Ingest crypto **news** and **social** data from *every free platform* continuously, each
platform on its own independent self-throttling poll loop (fan-out, **not** failover),
persist every item to Postgres, and compute sentiment from the stored items. When a
platform hits its rate limit, that platform's loop **pauses until the limit resets**
(learned from the API's own rate-limit headers, or a configured known limit) and then
resumes — no fallback to another source.

## Motivation

The current pipeline (post-cascade) feeds sentiment from a small set of sources via a
primary→fallback cascade. The user wants **breadth** (all free platforms simultaneously),
**durable storage** (a queryable history + dedup), and **per-platform rate-limit
self-management**. Fan-out collection maximizes coverage; DB-as-source decouples ingestion
from scoring and gives an auditable record; adaptive throttling keeps every free tier alive
without dropping a source.

## Architecture & data flow

```
collector-social ─ N independent provider loops ─┐
collector-news   ─ N independent provider loops ─┼─► Postgres  raw_content
                                                  │      (dedup on (source, external_id))
        sentiment-service (DB-poll mode) ─────────┘
          1. SELECT items WHERE scored_at IS NULL LIMIT N
          2. score each item with the existing HF scorer
          3. UPDATE raw_content with score/confidence/model/scored_at
          4. UPSERT content_sentiment_agg (per symbol / window)
          5. publish SentimentEvent on Kafka  ──► decision-engine, ai-workers (UNCHANGED)
```

**Downstream is preserved.** Collectors stop emitting `SocialEvent`/`NewsEvent` on Kafka and
write to the DB instead. Only `sentiment-service`'s *input* changes (Kafka → DB polling); it
still *publishes* `SentimentEvent` to Kafka so `decision-engine` / `ai-worker-haiku` are
untouched.

### Units & boundaries

- **`RawItem`** (Pydantic model, `cmi_common`) — the normalized item every provider returns.
- **`Provider`** (evolved protocol) — `name`, `kind` (social|news), `fetch() -> list[RawItem]`,
  `close()`. Declares its rate-limit policy. No DB/Kafka knowledge.
- **`ContentRepository`** (`cmi_common`, DB layer) — `insert_items(items) -> int` using
  `INSERT ... ON CONFLICT (source, external_id) DO NOTHING`; `fetch_unscored(limit)`;
  `mark_scored(...)`; `upsert_aggregate(...)`.
- **`AdaptivePollLoop`** (`cmi_common`) — runs one provider forever: poll → persist → sleep
  its cadence; on `RateLimitedError` sleep until reset; coordinates via a Redis pause key.
- **Provider modules** — one file per platform under each service's `app/providers/`.
- **Sentiment DB worker** (`sentiment-service`) — the poll→score→aggregate→publish loop.

## Data model (Postgres / TimescaleDB)

### `raw_content` (hypertable on `fetched_at`)

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `source` | text | platform id (`bluesky`, `reddit`, `mastodon`, `farcaster`, `stocktwits`, `lens`, `fourchan`, `youtube`, `telegram`, `rss`, `google_news`, `gdelt`, `coinpaprika`, `cryptocompare`, `coingecko`, `messari`, `newsdata`) |
| `kind` | text | `social` \| `news` |
| `external_id` | text | provider's stable id / guid |
| `url` | text null | |
| `author` | text null | |
| `title` | text null | news |
| `text` | text | body / post text scored by sentiment |
| `symbols` | text[] | detected tickers (may be empty) |
| `engagement` | double precision null | social: likes+reposts+replies etc. |
| `lang` | text null | |
| `published_at` | timestamptz null | source-provided |
| `fetched_at` | timestamptz not null default now() | hypertable time dim |
| `sentiment_score` | double precision null | filled by worker |
| `sentiment_confidence` | double precision null | |
| `sentiment_model` | text null | |
| `scored_at` | timestamptz null | null = unprocessed |

Constraints/indexes: `UNIQUE (source, external_id)`; partial index
`(fetched_at) WHERE scored_at IS NULL` for the worker's queue scan.

### `content_sentiment_agg` (derived rollup, maintained by the worker)

| Column | Type | Notes |
|---|---|---|
| `symbol` | text | `MARKET` when no ticker detected |
| `kind` | text | `social` \| `news` |
| `window_start` | timestamptz | bucket start |
| `window_size` | interval | e.g. 60 min |
| `mentions` | int | |
| `unique_authors` | int | |
| `engagement_sum` | double precision | |
| `avg_sentiment` | double precision | mean of item scores |
| `weighted_sentiment` | double precision | engagement/confidence-weighted |
| `updated_at` | timestamptz | |

PK `(symbol, kind, window_start, window_size)`. Maintained by upsert after each scoring
batch (simple, avoids TimescaleDB continuous-aggregate coupling to the async score column).

Alembic migration creates both tables (+ hypertable + indexes).

## Adaptive rate-limit self-management

Each provider owns its throttle; there is **no cross-source failover**.

- **Proactive quota guard:** provider declares `rate_limit=(calls, window_seconds)`; a Redis
  token bucket (`cache.allow`) blocks a poll that would exceed it → the loop waits.
- **Reactive, API-driven re-adjustment:** on HTTP 429 (or provider-signaled exhaustion), the
  provider raises `RateLimitedError(retry_after)`. `retry_after` is derived from the
  response headers when present, in priority order: `Retry-After` (secs or HTTP date) →
  `X-RateLimit-Reset` / `x-ratelimit-reset` (epoch or secs) → provider's configured window.
  A shared helper `parse_retry_after(response, default)` centralizes this.
- **Resume:** `AdaptivePollLoop` sleeps `retry_after`, sets a Redis pause key
  (`ratelimit:<source>` with TTL = retry_after) so replicas coordinate, then resumes the
  **same** provider. Normal cadence between successful polls = provider poll interval.
- **Reuse:** keep `RateLimitedError`; repurpose `CircuitBreaker` as the Redis pause gate
  (is_open/trip already model "paused until TTL"); **retire `SourceCascade`**.

## Sentiment worker (in `sentiment-service`)

Replace the Kafka news/social consumer with a `run_periodic` DB loop:
`fetch_unscored(batch)` → score each `text` (news: `title + text`) with the existing HF
scorer → `mark_scored` → `upsert_aggregate` per detected symbol (or `MARKET`). Then publish
**one `SentimentEvent` per (scored item × detected symbol)** — fanning out over the item's
symbols, or `["MARKET"]` when none — exactly mirroring the current `_handle_news` fan-out so
`decision-engine` keeps receiving per-symbol sentiment. `SentimentEvent` schema and Kafka
output are unchanged.

## Platform inventory (all free; key-gated ones no-op without their env)

**Social (`collector-social`)**
| Platform | Key? | Env / notes |
|---|---|---|
| Bluesky | none | keyless public `searchPosts` (already built — migrate to `RawItem`/DB) |
| Mastodon | none/token | public timeline/hashtag API; instance configurable |
| 4chan /biz/ | none | unofficial JSON API; noisy |
| Reddit | optional creds | `/new`; public `.json` fallback (already built — migrate) |
| Farcaster | key | Neynar free tier (`NEYNAR_API_KEY`) — crypto-native |
| StockTwits | optional | crypto cashtag stream + bull/bear tag |
| Lens | key | decentralized social |
| YouTube | key | Data API; crypto video titles/comments |
| Telegram | token+session | MTProto/Telethon public channels; built last |

**News (`collector-news`)**
| Platform | Key? | Env / notes |
|---|---|---|
| RSS | none | configurable feed list (already built — migrate) |
| Google News RSS | none | `?q=crypto` RSS |
| GDELT | none | global news + tone |
| Coinpaprika | none | news/events |
| CryptoCompare | key | `/data/v2/news` (already built — migrate) |
| CoinGecko news | key | `/news` (key already used for market data) |
| Messari | key | news/research, asset-tagged |
| NewsData.io | key | crypto-filtered news |

Each key-gated provider checks its env at startup and logs "disabled (no key)" + is skipped —
mirroring the retired `collector-twitter` no-op pattern.

## What is removed / repurposed

- **Removed:** `SourceCascade` (failover) and the two services' cascade wiring; the Kafka
  `SocialEvent`/`NewsEvent` producer path from collectors; `sentiment-service`'s Kafka
  news/social consumer.
- **Kept/evolved:** `RateLimitedError`, `CircuitBreaker` (→ pause gate), `Provider` protocol
  (→ returns `RawItem`, adds `kind`), the four existing provider bodies (Bluesky, Reddit,
  CryptoCompare, RSS) rewired to the new model, the HF scorer.
- `SocialEvent`/`NewsEvent` Pydantic models may remain for compatibility but are no longer on
  the hot path; decide during planning whether to keep or drop (YAGNI leans drop if unused).

## Build sequencing (each phase shippable; sequenced by the implementation plan)

1. **Framework** — Alembic migration (`raw_content`, `content_sentiment_agg`, hypertable,
   indexes); `RawItem` model; `ContentRepository`; `AdaptivePollLoop` + `parse_retry_after`;
   evolve `Provider`; retire `SourceCascade`.
2. **Rewire existing 4 sources** (Bluesky, Reddit, CryptoCompare, RSS) to `RawItem` + DB;
   convert both services to fan-out loops. End-to-end works with existing sources.
3. **`sentiment-service` DB mode** — poll→score→aggregate→publish; drop Kafka social/news
   consumer; keep `SentimentEvent` output.
4. **New providers** — keyless first (Mastodon, 4chan, Google News RSS, GDELT, Coinpaprika),
   then key-gated (Farcaster, StockTwits, Lens, YouTube, CoinGecko news, Messari, NewsData),
   Telegram last.
5. **Ops** — docker-compose env, `.env.example`, Prometheus targets, README/docs, memory.

## Testing strategy

- **Providers:** `respx`-mocked HTTP → assert normalized `RawItem` list (fields, symbol
  extraction, dedup id) and `RateLimitedError` on 429 with correct `retry_after` from headers.
- **`parse_retry_after`:** unit tests for `Retry-After` seconds/HTTP-date, `X-RateLimit-Reset`
  epoch/secs, and default fallback.
- **`ContentRepository`:** against a test Postgres (or the project's DB test fixture) — insert
  dedup (ON CONFLICT), `fetch_unscored`, `mark_scored`, `upsert_aggregate` idempotency.
- **`AdaptivePollLoop`:** fake provider + fake clock/cache — verify pause-until-reset and
  resume-same-source (no failover).
- **Sentiment worker:** seed unscored rows → run one tick → assert rows scored, aggregate
  upserted, `SentimentEvent` published.
- **Key-gated no-op:** provider with missing env is skipped and never hits the network.

## Risks / open items (resolve in planning)

- **Scoring volume:** per-item scoring across many platforms — batch size + poll cadence must
  be tuned so the HF scorer keeps up; the `scored_at IS NULL` queue naturally backpressures.
- **DB test harness:** confirm the repo has an async Postgres test fixture; if not, the plan
  adds one (or uses a lightweight schema-on-sqlite shim only where TimescaleDB features aren't
  exercised).
- **Per-platform id/field mapping & symbol extraction** differ per API — each provider test
  pins its mapping.
- **`MARKET` fallback** for symbol-less items (esp. RSS/GDELT) — same known trade-off as
  today; per-symbol signal is sparse for those sources by nature.
- **Telegram** needs a pre-authenticated session; treated as the last, optional provider.
