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
4. **Set `RISK_MIN_CONFIDENCE` in the same change**, or the risk engine silently tightens.
   Confidence now measures seven axes, so a legacy-only symbol — "most of them most of the
   time" — tops out at **0.75** where it used to reach 1.0. Against an unchanged
   `RISK_MIN_CONFIDENCE=0.55` (`risk-engine/app/main.py:23`) the floor effectively becomes 0.733
   on the old scale, and symbols that used to pass at 0.60 no longer do. `0.55 × 0.75 ≈ 0.41`
   preserves the admission set.

   A second effect has no env knob: `risk-engine/app/rules.py:88` sizes positions as
   `max_position_pct × min(1.0, confidence)`, so every surviving legacy-only position is sized
   **25% smaller** than before. That is a real change to live trading, not a rounding detail —
   decide it deliberately rather than discovering it.

5. Watch the decision rate for 24h. Retuning the threshold is an env change and a restart, not a
   redeploy — only the model itself needs one.

### Do NOT use a SQL ratio to pick the threshold

An earlier version of this note proposed `percentile_disc(...) ORDER BY score::float /
confidence` over `decision_journal`. **That is wrong twice over**, and both errors push the
threshold *too high* — which would restore the exact deadlock this change removes, presenting
as a quiet pipeline with no error anywhere.

1. **Those columns are not this model's output.** `ai-worker-sonnet/app/journal.py:63-64` writes
   `score`/`confidence` from `analysis.*`, i.e. **haiku's four-factor scorer**. Haiku's
   confidence is `0.25 + 0.35·liq + 0.4·(present/4)` — a hand-tuned affine floored at 0.25,
   with no relation to present weight. Dividing by it multiplies sparse rows by up to 4×.
2. **The identity does not hold where it matters.** v1's `_norm_news` returned 0.25 with all
   inputs absent and that value entered the numerator, while `_signal_present` reported the axis
   *absent* — so `score_v1 / confidence_v1` overestimates `score_v2` by `5/confidence` points on
   every row without its own news, which is most of them. Measured over 30k samples: 24%
   violated by >1 point, worst case +33.8. `test_migration_identity_holds_on_unrounded_values`
   does not catch this — it hardcodes `v1_confidence = 1.0`, the one configuration where the
   identity cannot fail.

**Instead, recompute.** `decision_journal.features` stores the raw feature dict for every
analysis, escalated or not. Load the last 7 days, run each row through the new `score()`, and
take the percentile that preserves the current *decision-engine* pass rate:

```python
# scripts/pick_threshold.py — offline, reads only.
rows = session.execute(
    select(DecisionJournal.features).where(
        DecisionJournal.time > datetime.now(UTC) - timedelta(days=7)
    )
).scalars().all()
scores = sorted(
    r.opportunity_score
    for r in (score(features_from(f)) for f in rows)
    if r.confidence > 0                      # the evidence floor emitted nothing
)
# current rate: the fraction of Decision rows vs DecisionJournal rows over the
# same window, since only cleared decisions reach the Decision table.
print(scores[int((1 - current_rate) * len(scores))])
```

`features_from` is the same mapping `engine.py:126-142` performs — keep them in one place if
this script outlives the deploy.

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

0. **`test_migration_identity_holds_on_unrounded_values` is a tautology.** It fixes
   `v1_confidence = 1.0`, where the claim reduces to `score_v2 == score_v1` — true purely
   because the ×0.75 rescale cancels, independent of renormalisation. Either sweep partial
   presence against a checked-in copy of the v1 function, or delete it: with the threshold now
   chosen by recompute, the identity is a nice-to-have rather than load-bearing, and a test that
   cannot fail is worse than no test.

1. **The negative-TVL guard** above — the one measured crash path still unhandled.
2. **T7–T14 are unreviewed.** T12 in particular changes production scoring.
3. `UPSTREAM_REQUESTS` records `next_unlock`'s deliberate `ValueError` as `status="ok"`, because
   it is raised after `_get` returns. An unread-schedule counter is the natural counterpart to
   the `throttled` label the client already has.
4. The long/short (`k=1.5`) and open-interest (`/20.0`) scales are reasoned, not measured — the
   obvious first candidates if the positioning axis underperforms.
