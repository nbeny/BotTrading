# Status — derivatives & fundamentals, scoring v2

**All 14 tasks implemented.** Branch `feat/derivatives-fundamentals` in
`.worktrees/derivatives-fundamentals`, 32 commits on `8f1372f`. Working tree clean,
full suite **exit 0**, no lint debt added.

**Plan:** `2026-07-31-derivatives-fundamentals-scoring-v2.md`
**Spec:** `../specs/2026-07-31-derivatives-fundamentals-scoring-v2-design.md`

## What shipped

Two keyless collectors — `collector-defillama` (TVL, fees, token unlock schedules) and
`collector-binance-futures` (funding, open interest, long/short) — publishing typed events on
two new topics, folded into `ai-worker-haiku`'s feature store and scored by a
**seven-axis model that renormalises over present weight**.

## Review status — read this before merging

| Task | Implemented | Spec review | Quality review |
|---|---|---|---|
| T1 events + topics | ✅ | ✅ | ✅ |
| T2 shared universe | ✅ | ✅ | ✅ |
| T3 unlock parser | ✅ | ✅ | ✅ |
| T4 mapper | ✅ | ✅ (pre-rollup) | ⚠️ rollup fix unreviewed |
| T5 client | ✅ | ✅ | ✅ (re-review interrupted) |
| T6 collector | ✅ | ✅ | ✅ → Critical fixed, fix unreviewed |
| T7 Binance mapping | ✅ | ✅ | ❌ never ran |
| T8 Binance client | ✅ | ❌ never ran | ❌ never ran |
| T9 Binance cycle | ✅ | ❌ | ❌ |
| T10 haiku ingestion | ✅ | ❌ | ❌ |
| T11 new axes | ✅ | ❌ | ❌ |
| T12 renormalisation | ✅ | ❌ | ❌ |
| T13 engine wiring | ✅ | ❌ | ❌ |
| T14 deploy wiring | ✅ | ❌ | ❌ |

T1–T6 went through the two-stage review that found fourteen defects. **T7–T14 did not** — a
session limit ended the agent fleet, and the rest was implemented directly. They are tested and
linted, not reviewed. Given that every one of those fourteen defects was silent, that gap is the
main risk in this branch.

## Deployment procedure

The order matters; step 3 is the one that changes trading behaviour.

1. Deploy both collectors. Confirm DefiLlama publishes events and the Binance broad tier is not
   empty (a geo-block shows as a WARNING after three cycles).
2. Wait ~30 min, then confirm the axes populate: pick a major from `decision_journal.features`
   and check `funding_rate_8h` is present.
3. Run the two queries below against production, set `DECISION_THRESHOLD` to the result, and
   deploy the scoring change.
4. Watch the decision rate for 24h. Retuning the threshold is an env change and a restart, not a
   redeploy — only the model itself needs one.

```sql
-- current decision rate
SELECT count(*) FILTER (WHERE score >= 70)::float / count(*)
FROM decision_journal WHERE time > now() - interval '7 days';

-- the v2 threshold preserving it (substitute <rate> above)
SELECT percentile_disc(1 - <rate>) WITHIN GROUP (ORDER BY score::float / confidence)
FROM decision_journal WHERE time > now() - interval '7 days' AND confidence > 0;
```

The second query works because `score_v2 = score_v1 / confidence_v1` is an identity on
legacy-only features — pinned by `test_migration_identity_holds_on_unrounded_values`. If that
test ever breaks, the deployed threshold is silently wrong.

## The rule this work is built on

Fourteen defects were found, every one in the specification rather than in an implementer's
execution, and **every one silent** — no exception, no error log, no failing test. The shape
recurs because scoring v2 renormalises:

> An absent axis is *excluded*, not penalised. So an unmeasured value arriving as a confident
> reading always moves the score in the direction of that reading.

Concretely, what that produced: an unsizable unlock scored as a *perfect* fundamentals reading;
a protocol earning $0 in fees reported as unknown, so the dead protocol outscored the live one;
a throttled fetch indistinguishable from a clean bill of health; 37 of 40 tokens declaring "no
schedule known" about schedules sitting in Redis. One went the other way — Jupiter shipping a
confident `Decimal(0)` against $2.04B of TVL — which is why "it always inflates" is too
confident a generalisation.

The habit worth keeping: at every hop, ask whether `None` and `0` can still be told apart, and
probe the live API rather than trusting a number in a document — including the ones here. The
costliest defect came from misreading my own query output, not from failing to run one.

## Verified live-API facts

- `/overview/fees` carries **no** `gecko_id` (0 of 2,514 rows) and no `change_7dover7d`; needs
  `excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true` or it is 24.6 MB.
- Unlock schedules are **not** free: `api.llama.fi/emissions` → 402. Use
  `defillama-datasets.llama.fi/emissions/{slug}`, ~2.25 MB per protocol.
- Emissions slugs are **parent** slugs — `aave`, never `aave-v2`. Matching on `slug` covers 220
  of 359; via `parentProtocol`, 335.
- **No two protocol rows share a `gecko_id`.** Only `aave-v2` ($109M) carries `aave`; `aave-v3`
  ($13.7B) carries `None`. Family rollup through `parentProtocol` is mandatory.
- `pinjam-labs` has `tvl: -1654.77` while `tvl_usd` is `Field(ge=0)` — **still open**: one
  negative row raises `ValidationError` in the emit loop and publishes nothing for any token
  that cycle. Guard it before this sees production volume.
- Binance funding across 854 perps: p05 −0.000156, median +0.000050, p95 +0.000159. The
  normalisation divisor is `0.0001`; `0.0004` spanned only 0.19 across p05–p95.
- `openInterestHist?period=1h&limit=25` spans exactly 24 h and carries USD **and** base units.

## Known open items

1. **The negative-TVL guard** above — the one measured crash path still unhandled.
2. **T7–T14 are unreviewed.** T12 in particular changes production scoring.
3. `UPSTREAM_REQUESTS` records `next_unlock`'s deliberate `ValueError` as `status="ok"`, because
   it is raised after `_get` returns. An unread-schedule counter is the natural counterpart to
   the `throttled` label the client already has.
4. The long/short (`k=1.5`) and open-interest (`/20.0`) scales are reasoned, not measured — the
   obvious first candidates if the positioning axis underperforms.
