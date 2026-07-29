# Market Data Foundation (Lot 1) — Design

**Date:** 2026-07-29
**Status:** Approved (design), pending implementation plan
**Services touched:** `collector-kraken` (new), `api-gateway`, `libs/cmi_common`, `migrations`
**Sequence:** Lot 1 of 3. Lot 2 = fusion engine + replay harness. Lot 3 = ATR risk + threshold recalibration.

## Problem

Production shows an operational terminal with no data behind half of it, and a decision
pipeline that cannot ever fire. Measured on the VPS on 2026-07-29:

1. **`/market/news` returns `[]` forever.** It reads the `news` table, which has 0 rows.
   `collector-news` writes to `raw_content` (1,178 rows). No code anywhere writes `news`.
2. **`sentiment_score` is always `0.0`.** `read_api.py` reads the `sentiments` table, which
   has 0 rows. `sentiment-service` writes `content_sentiment_agg` (908 rows, 154 symbols).
   No code anywhere writes `sentiments`.
3. **`liquidity_usd` is hard-coded `0.0`** (`read_api.py:142`), so the terminal reports every
   token as having no liquidity, and the scorer falls back to a 24h-volume proxy.
4. **Token names are missing.** The `tokens` table has 0 rows, so `map_token` falls back to
   the symbol: the terminal shows `2Z`, `A7A5`, `USTBL` with no name, rank or real trending
   flag — even though `PriceEvent` already carries `coin_id`, `market_cap_rank` and
   `is_trending`.
5. **There are no candles at all.** The only price history is `prices`: 60-second ticks over
   ~4 days, whose `volume_24h_usd` is a rolling 24h total and therefore cannot be turned into
   per-candle volume. No OHLC, no per-candle volume, no order book, no spread.

Consequence: the decision layer scores momentum and volume from ticks, treats absent
liquidity as a neutral 0.5, and has no notion of where price sits in its own range. Sentiment
never reaches the read plane at all.

Two empty tables (`news`, `sentiments`) that are declared, imported and read — but written by
nobody — are the direct cause of items 1 and 2. The offline contract test passes on both,
because it asserts key *shapes* and never asserts that anything is in them.

## Goals

- Every field the frontend read contract already requires is backed by real data.
- Introduce true OHLC candles and measured order-book liquidity, sourced from the venue the
  bot actually trades on (Kraken spot).
- Derive the tradability filter and the two-regime universe split from data, not constants.
- Leave a clean, shared read interface (`SqlCandleReader`) for Lot 2 to build indicators on.
- Remove the dead tables so the failure mode cannot recur.

## Non-goals (explicitly deferred)

- **No indicators.** RSI, EMA, ATR, Donchian position are Lot 2. This lot stores the inputs
  and exposes the reader; it computes no trading signal.
- **No changes to the decision path.** `decision-engine`, `risk-engine` and `trading-engine`
  are untouched. In particular the `RISK_MIN_SCORE=70` / max-observed-score-68 deadlock is
  real and blocking, but it is Lot 3 — fixing it here would mean recalibrating against a
  scorer that is about to change.
- **No Fear & Greed / BTC dominance ingestion.** Redundant with the `MARKET` pseudo-symbol
  that `content_sentiment_agg` already maintains; revisit in Lot 2 if the regime signal is
  weak.

## Universe and the two regimes

The user chose a two-regime strategy: majors get sentiment × curve fusion at normal position
size; alts get curve-only signals at reduced size and a higher threshold. Lot 1 must produce
the data that makes that split possible, and the split itself must be **data-derived**:

- **Universe** = Kraken USD-quoted tradable pairs ∩ symbols seen in `prices` in the last 24h.
  Expected ~150-200 symbols.
- **Majors** = universe members with **≥ 10 mentions over a trailing 7 days** in
  `content_sentiment_agg` (both `kind`s summed), tunable via `KRAKEN_MAJOR_MIN_MENTIONS_7D`.

  Measured today that threshold yields ~11 symbols: BTC (421 mentions), ETH (163), XRP (132),
  SOL (67), HYPE (27), SHIB (24), LINK (16), DOGE (13), ZEC (12), DOT (11), USDT (11) — then a
  cliff into single digits. A threshold of 25 would keep only 5 symbols, which is too narrow to
  be a regime; 10 is the knee of the observed distribution. Note the aggregate table itself only
  goes back to 2026-07-22, so these counts are a 7-day floor and will rise as history accrues —
  which is why the threshold is a tunable and the set is recomputed rather than frozen.

Recomputed every cycle, so a token that becomes a topic of conversation joins the majors set
on its own. No hard-coded symbol list anywhere.

Measured justification for the split: sentiment coverage is extremely concentrated. 154
symbols have at least one mention, but only ~10 have usable depth. The two symbols the bot
has actually produced decisions on (DEXE, AERO) have **zero** sentiment — which is exactly
why a single blended strategy over the whole book is not viable.

## Architecture

Three touch points. Nothing in the decision path.

```
Kraken public API ──► collector-kraken ──► Postgres: candles, market_depth, venue_pairs
                                                │
CoinGecko ──► PriceEvent ──► api-gateway persister ──► Postgres: prices, tokens
                                                │
                                                ▼
                       read_api.py ◄── SqlCandleReader (cmi_common/sources/candles.py)
                                   ◄── SqlSentimentAggReader (existing)
```

### `collector-kraken` (new service)

Follows the repo's clean layout (`domain/`, `application/`, `infrastructure/`, `api/`,
`deps.py`, `/health`, `/metrics`). Writes only to Postgres — no Kafka. An hourly candle is
reference data to query, not an event to react to; routing it through Kafka would add
plumbing and inflate `events_market` for no consumer.

Note: `AdaptivePollLoop` (`cmi_common/sources/loop.py`) is **not** reusable here — it is typed
on `ContentRepository`/`RawItem`, i.e. textual content. The reusable piece is `Cache.allow()`,
the token-bucket that carries quota budgets. This service gets its own short loop on top of it.

**1. Resolve the universe.** At startup and once daily, one call to `/0/public/AssetPairs`
yields every tradable pair, its `wsname` (`XBT/USD`) and its `ordermin` — the minimum order
size, which matters directly on a $97 account. Kraken ticker normalization (`XBT`→`BTC`,
`XDG`→`DOGE`) is done by parsing `wsname` in a pure `domain/` function.

**2. Sweep candles.** Two independent loops:

| loop | universe | interval | cadence | calls/cycle |
|---|---|---|---|---|
| broad | full universe (~150-200) | 1h | 15 min | ~200 OHLC + ~200 Depth |
| majors | sentiment-rich subset (~11 today) | 15m | 5 min | ~11 OHLC |

Steady-state budget ≈ 0.5 req/s against a ~1 req/s public tolerance — half the quota, leaving
headroom for catch-up after an outage.

OHLC calls are **incremental**: `since` is set to the last stored candle, so a normal cycle
fetches a handful of candles, and a restart after downtime automatically backfills up to the
720 candles Kraken returns per call.

**3. Measure liquidity.** `/0/public/Depth` gives the order book; from it we derive spread %
and notional depth within ±1% of mid. This replaces the hard-coded `liquidity_usd = 0.0` and
also answers whether a $20 order fills without slippage.

### Schema

**`candles`** (hypertable) — PK `(time, symbol, interval)`, columns `open`, `high`, `low`,
`close`, `volume`, `vwap`, `trades`, `source`. Upsert is `ON CONFLICT DO UPDATE`, not
`DO NOTHING`: the forming candle is rewritten on every sweep until it closes. One retention
policy at 90 days covering both intervals — enough for Lot 2's replay, and splitting the table
by granularity would save a negligible amount of storage.

**The most recent candle is incomplete.** Computing an RSI on it is the classic bug in this
class of system. Rather than a `closed` boolean column — which lies the moment a writer
forgets to maintain it — closedness is **derived** by a pure function
`is_closed(bucket, interval, now)`, and the reader excludes the current bucket by default.

**`market_depth`** (hypertable) — PK `(time, symbol)`, columns `mid_price`, `spread_pct`,
`bid_depth_usd`, `ask_depth_usd`. Retention 7 days.

**`venue_pairs`** (reference table, not a time series) — PK `(venue, symbol)`, columns `pair`,
`ordermin`, `tradable`, `ambiguous`, `updated_at`. This is the Lot-3 tradability filter,
materialized. A dedicated table rather than a JSONB key inside `tokens` keeps **one writer per
table**: `tokens` is written by the persister, `venue_pairs` by this collector, and neither has
to merge JSONB under the other's hand.

### `SqlCandleReader` (`libs/cmi_common/cmi_common/sources/candles.py`)

Mirrors the existing `SqlSentimentAggReader` split: pure functions (`interval_delta`,
`is_closed`) unit-tested without a database, and a class that runs the queries.

- `series(symbol, interval, points, closed_only=True) -> list[Candle]` — oldest first.
- `latest(symbols, interval) -> dict[str, Candle]` — one query, not N.
- `latest_depth(symbols) -> dict[str, Depth | None]`.

No indicators in this lot. The boundary is placed; it is not crossed.

### Read plane rewiring (`api-gateway/app/read_api.py`)

No response shape changes. `read_contract.py` already requires `liquidity_usd` and
`sentiment_score` on `market/tokens`, and all seven keys on `market/news`. Only the
*provenance* of the values changes, so `tests/test_read_contract.py` stays green by
construction.

| endpoint / field | before | after |
|---|---|---|
| `/market/news` | `news` table (empty) | `raw_content` where `kind='news'` |
| `sentiment_score` | `sentiments` table (empty) | `content_sentiment_agg`, 24h confidence-weighted mean |
| `liquidity_usd` | hard-coded `0.0` | latest `market_depth` (bid + ask within ±1%) |
| `name` / `coin_id` / `is_trending` | symbol fallback, `change >= 5` heuristic | `tokens` table, real CoinGecko flag |

`raw_content.published_at` is `timestamptz`, unlike `News.published_at` which was a unix-epoch
`BigInteger` — the mapper drops its `datetime.fromtimestamp` conversion and uses `_iso()`
directly.

Sentiment for all 221 tokens is read in **one grouped query** over `content_sentiment_agg`, not
221 queries; `map_token` receives a precomputed dict exactly as it already receives `opp` and
`meta`. `/market/tokens/{symbol}` gets the same enrichments as the list endpoint, which it does
not have today.

### `tokens` population

`PriceEvent` does **not** carry `name` today — it has `coin_id`, `market_cap_rank` and
`is_trending`, but the name lives only in the raw CoinGecko payload (`row["name"]`, already used
elsewhere in the collector). Add an optional `name: str | None` field to `PriceEvent`;
backward-compatible with already-archived events.

The persister upserts `tokens` on `coin_id` conflict. At 221 price events per minute, writing
on every event would be pure write amplification on data that changes once a day, so the
persister keeps an in-process cache `coin_id → (name, rank, trending)` and writes only on change,
or at most once per hour per token.

### Dead code removal

Delete the `News` and `Sentiment` models and drop the `news` and `sentiments` tables via an
Alembic migration. Both are empty, have no writer anywhere in the codebase, and their only
effect to date has been to make the terminal look like it was displaying data.

## Error handling

**Ticker ambiguity.** `prices` symbols are CoinGecko tickers, which are not unique — dozens of
tokens are called `SOL` or `APE`. The Kraken ticker is authoritative for what we trade, so a
naive symbol match could attach a real Kraken pair's candles to a worthless homonym. Rule: when
a symbol resolves to several `coin_id`s in `tokens`, keep the best `market_cap_rank` and record
`ambiguous=true` in `venue_pairs`. Testable, and it leaves a trace instead of a silence.

**Legitimate absence.** A CoinGecko-tracked token absent from Kraken will never have a candle or
a book. That is not an error, it is the "not tradable" fact — recorded once in `venue_pairs` with
`tradable=false`, with no per-cycle log spam.

**Staleness.** If the collector dies, candles freeze silently and every reader keeps computing on
the past. The service writes the last successful sweep timestamp per interval into
`service_health` (the table exists and `/systems/overview` reads it), plus a Prometheus staleness
gauge. The terminal shows the collector red rather than showing stale numbers as current.

**Measured vs unmeasured.** This project already hit this trap: the Haiku scorer had to introduce
`liquidity_source` to distinguish a measured liquidity from a volume-derived estimate. Same rule
here — `SqlCandleReader.latest_depth()` returns `None` for an unmeasured symbol, and only the HTTP
edge coerces to `0.0` to satisfy the TS contract. Lot 2 must never receive a zero that means
"unknown".

**Numerics and time.** Kraken returns strings; parse to `Decimal`, never `float`, consistent with
the existing `Numeric(38,12)` columns. Timestamps follow the same UTC convention as the other
hypertables — this database has already paid for one timezone bug in the persister.

## Testing

**Pure functions, no database:** `wsname` parsing and `XBT`/`XDG` normalization, `is_closed`,
spread and notional-depth computation from an order book, OHLC payload → candle mapping.

**Integration:** `SqlCandleReader` against Postgres, following the pattern already used for the
sentiment reader.

**No test touches the network:** a fake Kraken provider replays recorded payloads from fixtures.

**Contract:** `tests/test_read_contract.py` stays green unmodified, since no shape changes.

**Plausibility — the assertion that was missing.** The contract test checks keys, not values.
`/market/news` returned `[]` with a perfectly conformant shape, which is precisely why this bug
survived in production. `scripts/verify_read_live.py` gains plausibility assertions: news
non-empty, sentiment non-zero on at least one major, liquidity populated on Kraken pairs. This is
the check that would have caught today's problem, and the only one that prevents its recurrence.

## Success criteria

Verified against production after deploy:

1. `/market/news?limit=20` returns ≥ 1 item with a real title, source and `published_at`.
2. `/market/tokens` returns a non-zero `sentiment_score` for at least BTC, ETH, XRP and SOL.
3. `/market/tokens` returns a non-zero `liquidity_usd` for every symbol with a Kraken pair.
4. `/market/tokens` returns real `name` values (not the symbol echoed back) and the real
   CoinGecko `is_trending` flag.
5. `candles` contains ≥ 24 closed 1h candles for every universe symbol, and ≥ 48 closed 15m
   candles for every major.
6. `venue_pairs` marks every universe symbol `tradable=true` and carries a non-null `ordermin`.
7. `service_health` shows `collector-kraken` healthy with a sweep timestamp under 20 minutes old.
8. `news` and `sentiments` tables no longer exist.

## Open items for Lot 2

- The `MARKET` pseudo-symbol already aggregates 355 mentions and is the natural input for the
  global regime gate.
- Sentiment is in [-1, 1] but chronically bullish (mean +0.36; only 80 of 1,209 items negative),
  so the fusion must use deviation from each symbol's own baseline, never an absolute threshold.
- Price history is ~4 days deep today and grows from here, so early replay windows will be short.
- `RISK_MIN_RR = 1.5` is currently a tautology: with fixed 5%/10% stop/target the ratio is always
  exactly 2.0 and the test never rejects anything. ATR-based levels in Lot 3 make it meaningful.
