# Derivatives & Fundamentals Sources + Scoring v2 — Design

**Date:** 2026-07-31
**Status:** Approved (design), pending implementation plan
**Services touched:** `collector-defillama` (new), `collector-binance-futures` (new),
`ai-worker-haiku`, `decision-engine`, `libs/cmi_common`
**Relation to prior specs:** subsumes the "threshold recalibration" half of Lot 3 announced in
`2026-07-29-market-data-foundation-design.md`. The ATR-risk half of that lot is untouched and
remains open.

## Problem

The decision path scores a symbol on five axes — volume growth, social, news, market trend,
liquidity — all derived from price, DEX activity and text sentiment. Two entire classes of
publicly available, free signal are absent from it:

1. **Positioning.** Funding rate, open interest and the retail long/short ratio say how
   crowded a trade already is. A symbol whose price and sentiment both look excellent *because
   everyone is already long at 0.1% funding* is a different proposition from one that looks
   identical with neutral funding. The model cannot currently tell them apart.
2. **Fundamentals and scheduled dilution.** TVL trend and protocol fees say whether anything
   is actually being used. Token unlocks say that a known quantity of supply hits the market
   on a known date — the single most mechanically predictable bearish event in crypto, and one
   the platform is presently blind to.

There is a second, independent defect that adding axes would otherwise amplify.

**Absent data is scored as the worst case.** Every `_norm_*` in `decision-engine/app/scoring.py`
returns `0.0` when its input is `None`, and the weighted sum divides by a constant 1.0. A
symbol missing three of five signals is therefore not "scored on what we know" — it is
penalised for what we failed to collect. This is the documented `RISK_MIN_SCORE=70` /
max-observed-score-68 deadlock: the pipeline could not fire because the scale itself was
unreachable. The `_norm_news` docstring records the same bug being fixed once, locally, for
one axis.

Adding two axes worth 0.25 of weight that will be `None` for most symbols — only perp-listed
coins have funding, only DeFi protocols have TVL — would cap an otherwise perfect token at
**75/100** against a threshold of 70. The new signal would make the existing problem worse
before it made any decision better. So the two changes ship together, or neither does.

## Goals

- Ingest DefiLlama (TVL, fees, unlock schedules) and Binance futures (funding, open interest,
  long/short ratio) as first-class typed events on the bus.
- Extend the scoring model to seven axes: `positioning` (0.15) and `fundamentals` (0.10).
- Make an absent axis mean *absent* — excluded from the score — rather than *worst case*.
- Keep the change auditable: the transformation of any historical signal under the new model
  must be a closed-form identity, not an empirical mystery.

## Non-goals

- **No persistence of the new events.** They feed the feature store and nothing else. The
  Command Center pipeline graph and the Entonnoir keep their seven stages, sourced from
  Postgres as today; the two new collectors will not appear in them. Adding a table and a
  persister is a separate, later change.
- **No new direction.** `DecisionEvent` remains `Direction.LONG` only. An imminent unlock or
  extreme funding suppresses a score below threshold — it never opens a short.
- **No shadow mode, no replay harness.** The user chose a direct switch (see Rollout).
- **No new secrets.** Both APIs are keyless.
- **No ATR / position-sizing work.** Still Lot 3 of the market-data spec.

---

## Part 1 — Collection

Both services follow the `collector-coingecko` shape: `run_periodic` + client + collector,
Kafka producer, no database. They are *not* built on `AdaptivePollLoop`, which is bound to
`ContentRepository` and the `raw_content` path.

### `collector-defillama`

Cadence 600 s (`DEFILLAMA_POLL_INTERVAL`). Three bulk requests per cycle, independent of
universe size:

| Endpoint | Yields |
|---|---|
| `GET api.llama.fi/protocols` | TVL, `change_1d`, `change_7d`, `gecko_id` for all protocols |
| `GET api.llama.fi/api/emissions` | unlock schedules per token |
| `GET api.llama.fi/overview/fees` | 24h and 7d fees / revenue |

**Symbol mapping is by `gecko_id` → `Token.coin_id`, never by ticker.** A protocol without a
`gecko_id` is dropped rather than guessed. This deliberately sidesteps the ambiguity that
`collector-kraken`'s `ambiguous_symbols` exists to record.

When a protocol appears under several entries (parent/child protocols on DefiLlama), TVL is
summed at the `gecko_id` level before emission, so a token's TVL is the token's, not one
deployment's.

### `collector-binance-futures`

Cadence 300 s (`BINANCE_FUTURES_POLL_INTERVAL`), two tiers mirroring `CandleSweeper`:

- **broad** — `GET fapi.binance.com/fapi/v1/premiumIndex` with no parameters returns the
  funding rate for every perpetual in **one** request. Retained for symbols seen in `prices`
  over the last 24 hours.
- **majors** — for each major from `split_regimes` (~11 symbols at
  `KRAKEN_MAJOR_MIN_MENTIONS_7D=10`): `GET /fapi/v1/openInterest` and
  `GET /futures/data/globalLongShortAccountRatio`. ~22 requests.

Roughly 23 requests per 5-minute cycle against a 2400 weight/minute budget. The majors set is
read from the existing universe helpers rather than reimplemented.

`BTCUSDT → BTC` mapping strips the `USDT` quote and passes through the same ambiguity guard: a
ticker claimed by more than one `coin_id` is skipped, not arbitrated.

### Republication

**Both collectors publish every cycle, whether or not the value changed.** `FeatureStore` has
a 900 s TTL (`ai-worker-haiku/app/features.py:14`) while funding updates every 8 hours and TVL
daily. Without republication the features would expire between updates and the axes would
flicker in and out of the score. At ~200 symbols this costs ~40 events/min, which is well
inside the existing bus load.

### Events

Two topics, preserving the one-topic-one-event-type invariant of `TOPIC_EVENT`.

```python
class DerivativesEvent(BaseEvent):        # market.derivatives.events, 6 partitions
    event_type: Literal[EventType.DERIVATIVES] = EventType.DERIVATIVES
    symbol: str
    funding_rate_8h: float | None            # signed; ±0.001 is extreme
    funding_annualized_pct: float | None
    open_interest_usd: Decimal | None
    open_interest_change_pct_24h: float | None
    long_short_account_ratio: float | None
    def partition_key(self) -> str: return self.symbol


class FundamentalsEvent(BaseEvent):       # market.fundamentals.events, 3 partitions
    event_type: Literal[EventType.FUNDAMENTALS] = EventType.FUNDAMENTALS
    symbol: str
    coin_id: str
    tvl_usd: Decimal | None
    tvl_change_pct_7d: float | None
    fees_24h_usd: Decimal | None
    fees_change_pct_7d: float | None
    next_unlock_at: datetime | None          # None + schedule known ⇒ nothing within 30d
    next_unlock_pct_supply: float | None
    has_unlock_schedule: bool                # separates "no unlock" from "unknown"
    def partition_key(self) -> str: return self.symbol
```

`has_unlock_schedule` carries the unknown-vs-zero rule on its own. DefiLlama tracks emission
schedules for a minority of tokens; "absent from the dataset" must never be read as "no
dilution scheduled".

New `EventType` members `DERIVATIVES` and `FUNDAMENTALS`; new `Source` members `DEFILLAMA` and
`BINANCE_FUTURES`.

### Ingestion into the feature store

`HaikuWorker._extract` gains two branches writing into the per-symbol Redis hash:

- `DerivativesEvent` → `funding_rate_8h`, `open_interest_usd`,
  `open_interest_change_pct_24h`, `long_short_account_ratio`
- `FundamentalsEvent` → `tvl_usd`, `tvl_change_pct_7d`, `fees_change_pct_7d`,
  `next_unlock_at`, `next_unlock_pct_supply`, `has_unlock_schedule`

`FeatureStore.update` already drops `None` values, so a partial event never erases a known
field. The consumer subscribes to the two new topics.

`_ready()` is **not** relaxed: these are context signals, not triggers. A symbol with only
funding and no price anchor still does not get scored.

The features reach `decision-engine` through `AnalysisEvent.meta["features"]`, the same channel
`_liquidity()` already reads.

---

## Part 2 — Scoring v2

### New axis normalisations

Each axis is the **mean of its present terms**, so `positioning` exists on funding alone for
broad-tier symbols and sharpens on majors, without a missing term dragging it down.

```python
# positioning — contrarian on crowding, confirmatory on engagement
funding_term  = _sigmoid(-funding_rate_8h / 0.0004)      # +0.1% → 0.08 ; 0 → 0.5 ; -0.1% → 0.92
crowding_term = _sigmoid(-log(long_short_account_ratio), k=1.5)   # 2:1 long → 0.26 ; 1:1 → 0.5
oi_term       = _sigmoid(open_interest_change_pct_24h / 20)       # +20% → 0.73 ; 0 → 0.5

# fundamentals — trend, plus a dated dilution penalty
tvl_term    = _sigmoid(tvl_change_pct_7d / 15)
fees_term   = _sigmoid(fees_change_pct_7d / 25)
unlock_term = 1 - severity * proximity        # present only when has_unlock_schedule is True
    severity  = clamp(next_unlock_pct_supply / 5.0, 0, 1)    # 5% of supply = full severity
    proximity = clamp(1 - days_until / 30, 0, 1)             # beyond 30 days: no effect
```

**Units.** Every `*_pct_*` field is in percentage points (`5.0` means 5%), matching the
existing `price_change_pct_24h`. `funding_rate_8h` is the raw fraction Binance returns
(`0.0001` = 0.01%), which is why it carries no `pct` in its name; `funding_annualized_pct` is
percentage points. This convention is asserted by the normalisation unit tests.

Worked values: a 5% unlock in 3 days gives `unlock_term = 0.10`. A known schedule with nothing
inside 30 days gives `1.0` — a measurement, and a genuinely good one. A token DefiLlama does
not track produces no term at all.

Funding is contrarian by construction: positive funding means longs are paying shorts, which
is the crowded side. The sign convention must be asserted in a test, because getting it
backwards produces a model that is confidently wrong rather than obviously broken.

### Weights

```python
WEIGHTS = {
    "volume_growth":   0.1875,   "positioning":  0.1500,
    "social_score":    0.1500,   "fundamentals": 0.1000,
    "news_score":      0.1500,
    "market_trend":    0.1500,
    "liquidity_score": 0.1125,
}                                            # Σ = 1.0
```

The five existing axes are rescaled by ×0.75, preserving their relative proportions exactly.
This expresses no new opinion about the old model; it only makes room.

### Aggregation

```python
present        = {k: v for k, v in sub.items() if v is not None}
present_weight = sum(WEIGHTS[k] for k in present)
opportunity    = round(100 * sum(present[k] * WEIGHTS[k] for k in present) / present_weight)
confidence     = round(present_weight, 3)
```

`confidence` keeps its current meaning — the fraction of model weight backed by real evidence —
and `market_sentiment` stays excluded from it for the reason already documented: a market-wide
read is identical for every symbol, so counting it would lift confidence across the whole book
at once.

If `present_weight == 0` no score is emitted. In practice `_ready()` prevents this.

### The `None` change

The substantive edit is that `_norm_*` return `None`, not `0.0`, for absent input:

- `_norm_liquidity(None)` → `None`; `_norm_liquidity(0)` → `0.0`. A *measured* zero liquidity
  remains the worst score. An *unknown* liquidity leaves the calculation.
- `_norm_news` keeps its current behaviour — sentiment absent but market regime known is still
  scored — **except** where nothing at all is known, which moves from `0.5` to `None`. This
  serves the original fix's intent more faithfully: excluding an axis is neutral by
  construction, whereas returning `0.5` actively drags a strong symbol downward.
- `_norm_social(0.0)` stays present at `0.5`: zero growth is a measurement.

### Threshold

The ×0.75 rescale cancels in the division. For any symbol with no derivatives or fundamentals
data — which is the entire historical record — the identity is exact:

```
score_v2 = score_v1 / confidence_v1
```

because `confidence` already *is* the sum of present weights (`scoring.py:129-131`). It holds
on the unrounded weighted sums; `decision_journal.score` is stored as an integer, so a
threshold derived from stored rows carries up to one point of rounding slack. That is well
inside the precision at which a threshold is chosen at all, but it is the reason the unit test
below asserts the identity on unrounded values rather than on persisted ones.

The iso-rate threshold is readable directly from `decision_journal`, which stores `features`,
`score` and `confidence` for every analysis, escalated or not:

```sql
-- 1. current decision rate
SELECT count(*) FILTER (WHERE score >= 70)::float / count(*)
FROM decision_journal WHERE time > now() - interval '7 days';

-- 2. the v2 threshold that preserves it (substitute <rate> from step 1)
SELECT percentile_disc(1 - <rate>) WITHIN GROUP (ORDER BY score::float / confidence)
FROM decision_journal WHERE time > now() - interval '7 days' AND confidence > 0;
```

Two queries run at deploy time, not a code deliverable.

---

## Rollout

Direct switch, per the user's explicit choice. `score()` is replaced; there is no v1/v2 flag
and no shadow path.

The residual risk is bounded by two facts that were verified rather than assumed:
`DECISION_THRESHOLD` is already read from the environment (`decision-engine/app/main.py:15`),
so retuning or reverting the threshold costs a restart and no redeploy; and the trading engine
defaults to `dry_run` in compose, so a mis-set threshold changes what is *proposed* before it
changes what is *traded*.

Reverting the model itself does require a redeploy. That is the accepted cost.

Order of operations: deploy the two collectors first and confirm the axes populate, then
deploy the scoring change with the threshold from the queries above.

## Error handling

Both APIs are keyless, so there is no authentication failure mode. On error or rate-limit the
collectors back off and **publish nothing** — never a substitute zero. The axis simply becomes
absent and renormalisation absorbs it, which is precisely what the `None` change makes safe.

- Request budget via the existing `Cache.allow(name, max_calls, window)` primitive.
- `UPSTREAM_REQUESTS{service,provider,ok|error|ratelimit}`, same metric as the poll loops.
- Binance: read `X-MBX-USED-WEIGHT-1M` to self-throttle; honour `Retry-After` on 418/429.
- DefiLlama publishes no documented rate limit — fixed conservative cadence, no bursts.

**Geo-blocking watch.** Binance blocks some IP ranges. The Hostinger VPS is in the EU and
should be fine, but if the broad tier returns an empty set for three consecutive cycles the
collector logs at WARNING. Without it, `positioning` would sit permanently absent and
renormalisation would hide the outage perfectly — the failure mode this design is otherwise
built to avoid.

## Testing

- Pure unit tests per `_norm_*`, no I/O, matching the existing style of `scoring.py`.
- `sum(WEIGHTS.values()) == 1.0`.
- **Migration identity:** on a `Features` carrying only the five legacy axes, the unrounded v2
  weighted sum equals the unrounded v1 sum divided by v1 confidence, to floating-point
  tolerance. Asserted on unrounded values, computed from the same `Features` instance — not on
  the persisted integers, where rounding costs up to a point. This pins the relationship the
  threshold choice depends on; if it ever breaks, the deployed threshold is silently wrong.
- Funding sign: positive funding must lower `positioning`, asserted explicitly.
- Unknown-vs-zero: `_norm_liquidity(None) is None` and `_norm_liquidity(0) == 0.0`;
  `has_unlock_schedule=False` yields no `unlock_term` while a known empty schedule yields
  `1.0`.
- Mappers: `gecko_id` → `coin_id` join, `BTCUSDT` → `BTC`, ambiguous ticker rejected,
  parent/child TVL summed once.
- `TOPIC_EVENT` and `TOPIC_PARTITIONS` cover both new topics.
- Collector cycle tests against recorded fixtures, including an empty-response cycle that must
  publish nothing rather than zeros.

## Deployment artefacts

Two `Dockerfile`s and a `pyproject.toml` each — the omission fixed in `22caede` for
`collector-kraken`. Services added to `docker-compose.yml` and `docker-compose.vps.yml`,
entries added to the build matrix in `.github/workflows/deploy.yml`. No new secrets.

## Open question, deferred by choice

The new events are not persisted, so the two collectors are invisible to `/systems/overview`
and the Entonnoir. This is consistent — those seven stages model the decision pipeline, not
the source inventory — but it does mean a silent DefiLlama outage is only visible in metrics
and logs, not in the terminal. Revisit if that proves uncomfortable in operation.
