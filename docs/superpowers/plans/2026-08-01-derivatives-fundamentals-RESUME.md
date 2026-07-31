# Resume note — derivatives & fundamentals, scoring v2

**Stopped:** 2026-08-01, session token limit. Not a technical failure — five agents were
cut off mid-flight.

**Branch:** `feat/derivatives-fundamentals` in `.worktrees/derivatives-fundamentals`.
23 commits on top of `8f1372f`. Working tree clean, full suite **exit 0**.

**Plan:** `2026-07-31-derivatives-fundamentals-scoring-v2.md`
**Spec:** `../specs/2026-07-31-derivatives-fundamentals-scoring-v2-design.md`

Both have been corrected repeatedly during execution and are the current source of truth —
the plan's task bodies now reflect what the code actually does, not what was first written.

## Where each task stands

| Task | Implemented | Spec review | Quality review |
|---|---|---|---|
| T1 events + topics | ✅ | ✅ | ✅ |
| T2 shared universe | ✅ | ✅ | ✅ |
| T3 unlock parser | ✅ | ✅ | ✅ |
| T4 mapper | ✅ `df24c50` | ✅ (pre-rollup) | ⚠️ re-review needed |
| T5 client | ✅ `c9ac803` | ✅ | ⚠️ re-review interrupted |
| T6 collector | ✅ `cb9574f` | ✅ | ❌ **Critical open** |
| T7 Binance mapping | ✅ `3a893a4` | ✅ | ⚠️ never ran |
| T8 Binance client | ✅ `c22561a` | ⚠️ never ran | ⚠️ never ran |
| T9–T14 | not started | — | — |

**Two commits are UNREVIEWED** and labelled as such in their messages: `df24c50` (mapper
family rollup) and `c22561a` (Binance client). Both are green but neither review stage ran.

## The one open Critical — do this first

**T6's round-robin caps map *membership*, not fetches.** `_collect_unlocks` only inserts the
≤3 coin ids in the cursor window, so every other eligible token reports
`has_unlock_schedule=False` — "DefiLlama does not track this" — even when its schedule is
already sitting in Redis. `LlamaClient.unlock` checks the cache first, so those reads are free;
the cap suppresses them along with the expensive fetches.

Measured on a 40-protocol fake with everything cached: **3 tokens report a schedule, 37 report
unknown**, and each of those 37 drops the dilution axis and pushes a fundamentals score *up*.

The fix spans two files and is fully specified in the message sent to the T6 agent (see its
transcript), in summary:

1. Add `LlamaClient.cached_unlock(coin_id) -> tuple[bool, Unlock | None]` — cache-only, returns
   `(False, None)` on a miss, which must stay distinct from `(True, None)` = read-and-empty.
2. `_collect_unlocks` reads the cache for **every** eligible token, collects misses, and spends
   the fetch budget only on those. Keep the cursor over the misses so a persistently-failing
   protocol cannot starve the others.
3. Fix the comment on `DEFAULT_MAX_UNLOCK_FETCHES`, which states the mistaken belief in plain
   sight.
4. Add `test_cached_schedules_are_reported_for_every_eligible_token`.

Also open from that review: the `logger.info` line cannot separate "not attempted" from
"attempted and failed" from "nothing eligible"; there is no metric for an unread schedule (and
`next_unlock`'s `ValueError` is recorded as `status="ok"` because it is raised after `_get`
returned); and an empty token universe is indistinguishable from a legitimately empty one.

## What this exercise has been about

Thirteen defects were found in the specification I wrote, not in the agents' execution. Every
one was a **silent inversion** — no exception, no error log, no failing test. The recurring
shape:

> An unmeasured value reaches the scoring model as a confident reading.

That is dangerous here specifically because scoring v2 **renormalises over present axes**: an
absent axis is excluded, not penalised. So "we failed to measure this" scores *better* than
"we measured this and it is bad". The single most valuable habit for whoever continues: at
every layer, ask whether `None` and `0` can be told apart, and probe the live API rather than
trusting any number in this document — including the ones written by me, one of which came
from misreading my own query output.

## Verified live-API facts (do not re-derive)

- `/overview/fees` carries **no** `gecko_id` (0 of 2,514 rows) and no `change_7dover7d`; needs
  `excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true` or it is 24.6 MB.
- Unlock schedules are **not** free: `api.llama.fi/emissions` → 402. Use
  `defillama-datasets.llama.fi/emissions/{slug}`, ~2.25 MB per protocol.
- Emissions slugs are **parent** slugs — `aave`, never `aave-v2`. Matching on `slug` covers
  220 of 359; via `parentProtocol`, 335.
- **No two protocol rows share a `gecko_id`.** Family rollup through `parentProtocol` is
  mandatory, not defensive.
- `pinjam-labs` has `tvl: -1654.77` and `tvl_usd` is `Field(ge=0)` — one negative row raises
  `ValidationError` inside the emit loop and publishes **nothing for any token** that cycle.
  Guard still open.
- Binance funding across 854 perps: p05 −0.000156, median +0.000050, p95 +0.000159. The
  normalisation divisor is `0.0001`, not `0.0004`.
- `openInterestHist?period=1h&limit=25` spans exactly 24 h and carries USD **and** base units.

## Remaining sequence

T9 (Binance two-tier cycle) → T10 (haiku ingestion) → **T11–T12 (scoring v2)** → T13 (engine
wiring) → T14 (deploy).

T12 is the one that changes production behaviour. It rewrites three assertions in
`tests/test_scoring.py` that lock in the old "absent axis = 0.0" semantics — those must be
rewritten deliberately, with reasoning, not adjusted until green. It also carries the
migration-identity test (`score_v2 == score_v1 / confidence_v1` on legacy features), which is
what makes the deploy-time `DECISION_THRESHOLD` query trustworthy.
