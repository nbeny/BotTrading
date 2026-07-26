# Crypto Relevance & Symbol Resolution — Design

**Date:** 2026-07-26
**Status:** Delivered and running in production.

- **Phase 1** (normalization core) — shipped. Mis-attribution went from 62% of
  symbol assignments to 0%, measured on live rows before and after.
- **Phase 2** (data wipe) — done, 1757 rows destroyed after the gate was
  confirmed working. Repopulated under the gate with `no_symbol = 0` throughout.
- **Phase 3** (source revival) — 4chan and Farcaster produce for the first time
  ever; RSS widened and its redirect bug fixed; CryptoCompare key-gated after
  its free tier turned out to be gone; GDELT query narrowed.
- **Phase 4** (regime signal) — `MARKET` now reaches decision scoring. Two of the
  three new sources are blocked on operator actions and one is shelved; see the
  Added table for the probe evidence.

**Outstanding, none blocking:** see *Known issues / corrections to make*. Three
of the planned sources are out for reasons outside our control — Reddit closed
registration, CryptoPanic went paid-only, StockTwits blocks programmatic access
— leaving Telegram, which waits on channel selection.
**Services touched:** `libs/cmi_common`, `collector-social`, `collector-news`,
`collector-coingecko`, `sentiment-service`, `decision-engine`, `scripts/`

## Problem

The sentiment pipeline ingests, scores and aggregates content that is largely
mis-attributed or not about crypto at all. Measured on the production VPS
(`<VPS_HOST>`) on 2026-07-26, over 1654 aggregated mentions:

1. **Provider symbol tags are trusted blindly and are wrong.** `newsdata.py:68`
   copies NewsData's `coin` array verbatim. NewsData matches common English words
   as tickers, so an article titled *"Hyperliquid Price Prediction Today: Key
   Breakout Levels for HYPE"* is stored with `["JST","FLOW","KEEP","BTC","ONE",
   "USDT","NEAR"]` — and does not contain `HYPE`. *"Bitcoin Slides Under $64K"*
   is stored with `["ETH","BTC","ONE","USDT","REKT","BAND","USDC"]`.
   The resulting top symbols in `content_sentiment_agg` are `ONE` (119 mentions),
   `JST` (93), `KEEP` (42), `FORM` (31), `PEOPLE` (24) — `ONE` and `JST` both rank
   above `ETH` (61). Every bogus symbol emits its own `SentimentEvent` into the
   decision-engine.

2. **23% of all sentiment lands in a symbol nobody reads.** GDELT (156/156 rows),
   RSS (93/93) and YouTube (122/122) carry no symbols at all. The worker applies
   `symbols = row.symbols or ["MARKET"]` (`worker.py:67`), so 377 of 1654 mentions
   aggregate under the pseudo-symbol `MARKET`. The decision-engine keys on real
   symbols and never consumes it.

3. **No crypto-relevance gate.** GDELT's `cryptocurrency` query returns
   `caughtoffside.com` (football), `vesti.ru`, `niagarafallsreview.ca`,
   `washingtonian.com`, `foxsanantonio.com`. All of it is scored and aggregated.

4. **Four sources are configured but produce nothing.** Reddit
   (`REDDIT_CLIENT_ID`/`SECRET` empty in prod → falls back to the public `.json`
   endpoint, which Reddit 403s from datacenter IPs, as `reddit.py:44` documents);
   4chan and Farcaster (both drop every item lacking an explicit `$TICKER` cashtag
   — `fourchan.py:55`, `neynar.py:67`); CryptoCompare (no key, zero rows ever).
   CoinDesk's RSS URL returns 308 and is logged unreachable every cycle.

## Goals

- Only crypto-relevant content reaches `raw_content`.
- Symbol attribution is correct — no ticker is recorded unless it is genuinely
  the subject of the item.
- Crypto-relevant content with no identifiable symbol becomes a usable
  market-regime signal instead of dead weight.
- Revive the four dead sources and add three new free-tier ones.
- Zero paid API dependencies.

## Non-goals

- No LLM call on the ingestion path (see "Rejected alternatives").
- No re-tuning of the decision-engine's five scoring weights.
- No changes to the hourly-bucket aggregation model
  (see `2026-07-25-sentiment-aggregation-rework-design.md`).

## Decisions taken

| Question | Decision |
|---|---|
| Budget | Free tier only. No paid API. |
| Token universe | **Hybrid** — closed top-N universe recognised by name and ticker; out-of-universe tokens accepted only via explicit `$CASHTAG`. |
| Symbol-less crypto content | Keep `MARKET` and **wire it** into the decision-engine as a regime signal. |
| Sources | Revive existing + add CryptoPanic, StockTwits, Telegram. |
| Existing polluted data | **Wipe and re-collect** — truncate `raw_content`, `content_sentiment_agg`, `content_sentiment_agg_daily`. |

## Architecture

### Single choke point

Normalization happens in `AdaptivePollLoop.run()`
(`libs/cmi_common/cmi_common/sources/loop.py:64`), between `fetch()` and
`insert_items()`:

```python
items = await self._provider.fetch()
items = self._normalizer.apply(items)      # new
inserted = await self._repo.insert_items(items)
```

No provider is modified to gain the filter. Both collectors — and every future
provider — inherit it. The rule lives in exactly one place.

Two new modules in `libs/cmi_common/cmi_common/sources/`:

| Module | Exports | Depends on |
|---|---|---|
| `lexicon.py` | `SymbolLexicon` (immutable snapshot), `LexiconLoader` (Redis-backed, in-process cache) | `Cache` |
| `normalize.py` | `ContentNormalizer.apply(items) -> list[RawItem]` | `SymbolLexicon` only — pure, no I/O |

`ContentNormalizer` is pure and synchronous, so the whole rule set is unit-testable
offline with no DB, no Redis and no network. `LexiconLoader` holds all the I/O.

### The lexicon

`collector-coingecko` already fetches the top-N coins by market cap each cycle
(`application/collector.py:37`). It gains one write: the `{id, ticker, name}`
list to Redis key `lexicon:coins`, TTL 24h. Collectors read it through
`LexiconLoader`, which caches the parsed snapshot in-process and refreshes on a
configurable interval (default 15 min).

`SymbolLexicon` holds three structures:

- `by_ticker: dict[str, str]` — uppercase ticker → canonical symbol
- `by_name: dict[str, str]` — lowercased coin name and alias → canonical symbol
- `ambiguous: frozenset[str]` — tickers that are also common English words

`ambiguous` is **computed** rather than enumerated: it is the intersection of the
universe's tickers with a bundled common-English-word list, so it follows
universe rotation on its own.

Be honest about what that does and does not buy. The *derivation* is automatic;
the **word list itself is hand-curated, and is the one manual duty in the whole
design** — a ticker missing from it is believed on sight. Three review rounds
audited it against the 50-coin seed and passed; the first audit against a
top-200-shaped universe found `ATH`, `PUMP`, `APE`, `IP`, `ID`, `AI`, `LAYER`
and a dozen more still missing. `ATH` was the worst: it means "all-time high" in
most crypto copy, always co-occurs with crypto vocabulary so the item is kept,
and would have climbed the aggregate table exactly as `ONE` did. **Re-audit this
list against the live universe, not the seed, whenever the universe rotates.**

There is a recall cost, accepted deliberately: a homograph ticker now needs its
coin name or a cashtag, so `ARB unlock schedule tomorrow` resolves to nothing
unless "Arbitrum" appears. Crypto coverage almost always names the project, and
precision is the stated priority.

Against the seed universe the computed set is `ONE, CORE, FORM, PEOPLE, KEEP,
FLOW, NEAR, BAND, LINK, UNI, GAS, TIME, WIN, MASK, …` — exactly the false
positives observed in production.

**Cold-start fallback:** a seed list of ~50 major coins is bundled in the package.
An empty or unreachable Redis falls back to the seed rather than to an empty
lexicon, so a restart degrades recall instead of silently dropping all content.

### The three gates

`ContentNormalizer.apply` runs each item through, in order:

**Gate 1 — symbol resolution.** Candidates are derived **from the item's own text
only** (title + body), through three channels: explicit `$CASHTAG` matches;
uppercase ticker tokens on word boundaries; and full coin names from `by_name`.
Each candidate is then confirmed:

| Candidate | Rule |
|---|---|
| In universe, ticker not in `ambiguous` | accept, in any case — lowercase included |
| In universe, ticker in `ambiguous` | accept **only** if it arrived as an uppercase explicit cashtag, or the coin's name appears in the text |
| Not in universe | accept **only** if it arrived as an explicit cashtag **and** the item is otherwise crypto-relevant |

Three refinements were forced by review, each after a concrete false positive:

- **Cashtag bodies must be uppercase.** `$one million` and `$trillion` are
  spelled-out amounts, and the lowercase form resurrected `ONE` — the single
  worst production false positive — through a channel that also satisfies Gate 2,
  so the row skipped every other check.
- **An out-of-universe cashtag needs corroboration.** Alone it is just a
  `$`-prefixed word: `$TSLA and $NVDA are leading the rally` booked two equities.
  The channel stays open (it is how a new token gets in) but the item must say
  something crypto elsewhere. This matters most before StockTwits lands.
- **Lowercase is trusted for non-homograph tickers.** `btc is ripping right now`
  matched nothing and was *dropped*, not booked as `MARKET` — real content loss
  on the all-lowercase social sources, invisible because the drop counter
  conflated it with football articles.

**Coin names need the same guard as tickers, and word count is not the test.**
A name match is what corroborates an ambiguous ticker, so an unguarded name
unlocks both channels at once: "the market maker" booked MKR, "cash flow
analysis" booked FLOW. An initial fix exempted multi-word names on the theory
that prose cannot produce them by accident; "The Graph" (GRT, in the seed)
disproved it immediately — "as the graph shows, inflation cooled" booked GRT.
A name is prose when **every** word in it is ordinary English. Prose names live
in a separate index: they confirm nothing alone, but they corroborate their own
ticker, which is what keeps coins whose ticker *and* name are both English words
(Dash, Gala, Beam) reachable at all.

**Provider-supplied tags (NewsData `coin`, CryptoCompare `categories`, CryptoPanic
`currencies`) are discarded, not validated.** Validating a tag can only mean
"the ticker or the coin name appears in the text" — which is exactly what text
extraction already computes, so a validated-tag channel adds nothing and only
adds a way to be wrong. This is the change that removes the `ONE`/`JST`
pollution at its source: NewsData tagged the Bitcoin article `USDT, USDC, ETH,
ONE, REKT, BAND`, and none of those strings, nor their coin names, occur in the
article. Only `BTC` survives, via the word "Bitcoin".

Per-provider trusted-tag opt-in (plausible for StockTwits' user-declared
cashtags or CryptoPanic's editorial currencies) is a deliberate later extension,
to be justified by measurement rather than assumed now.

**Gate 2 — crypto relevance.** The item is kept if it has at least one confirmed
symbol, **or** the text is crypto-relevant. Otherwise it is dropped and never
inserted.

Relevance is not one flat word list. The vocabulary is split by specificity: one
**strong** term (blockchain, DeFi, stablecoin, altcoin, onchain, halving,
airdrop, …) carries an item alone, while **weak** terms (gas, mining, bridge,
custody, exchange, futures, leverage, wallet, whale, sec, …) need two distinct
hits. A single generic term admits far too much: "Gas prices drop across the
Midwest", "whale watching season opens" and "custody battle ends in family
court" are exactly the regional general-news shape this gate exists to reject,
and each hits precisely one generic term. Matching tolerates a plural "s".

**Gate 3 — MARKET fallback.** Relevant but symbol-less items get
`symbols = ["MARKET"]` assigned **at collection time**, so `raw_content` records
the fact explicitly instead of the worker inventing it later. The
`symbols or ["MARKET"]` fallback in `worker.py:67` is removed — the invariant
becomes "every stored row has at least one symbol".

**Observability:** a `CONTENT_DROPPED{service, source, reason}` Prometheus
counter, with `reason` in `{not_relevant, empty_text}`, exposed on the existing
`/metrics` endpoint. The filter's behaviour is measured, not assumed. (There is
no `no_confirmed_symbol` reason: an item with no confirmed symbol but with
crypto vocabulary is kept as `MARKET` by Gate 3 rather than dropped.)

Two silent-degradation paths are instrumented alongside it, because both fail
invisibly and both mis-attribute rather than error:

- `cmi_lexicon_coins{service}` plus a warning while the loader is serving the
  bundled seed. A collector resolving against 50 coins instead of the live 200 —
  because `collector-coingecko` is down or not yet deployed — books genuine
  symbols as `MARKET`, and nothing downstream can tell.
- A warning in the sentiment worker naming the row and source when a
  `raw_content` row carries no symbols. Removing the worker's `MARKET` fallback
  is what surfaces the broken invariant; without the log it would simply vanish.

The counter has **no denominator**: it reports items rejected, not the rejection
rate. Judging whether the gate filters too aggressively means counting
`raw_content` rows per source in the database.

### Wiring MARKET into decisions

`scoring.py`'s five weights sum to 1.0 and are tuned; adding a sixth weighted term
would force a re-tune. Instead:

- `Features` gains `market_sentiment: float | None`.
- `_norm_news` uses the symbol's own sentiment when present, and falls back to
  `market_sentiment` **damped halfway toward neutral** when absent — i.e. the
  effective value is `market_sentiment * 0.5`. A macro signal is real but not
  symbol-specific, so it must not carry the same weight as a direct one.
- Weights, `_signal_present` semantics and the 0-100 output range are unchanged.

Macro and regulatory content stops being discarded, and no existing tuning moves.

## Source changes

### Revived

| Source | Change | Note |
|---|---|---|
| Reddit | Application-only OAuth | **Requires operator action**: create an app at reddit.com/prefs/apps (free) and set `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`. Without credentials Reddit stays at 403 and the source stays dark. |
| 4chan | Drop `if not symbols: continue` (`fourchan.py:55`) | The normalizer decides now; /biz/ goes from 0 rows to real volume. |
| Farcaster | Drop `if not symbols: continue` (`neynar.py:67`) | Same. Key already present in prod. |
| CryptoCompare | **Key-gated, off by default** | The spec claimed the `/news` endpoint was free and keyless. Verified false: it answers `401 API key required` and points at developers.coindesk.com — CryptoCompare was folded into CoinDesk Data. Keyless it only burned a request and logged a failure every cycle, which is why it had zero rows *and* zero successful logs. A free key re-enables it. |
| RSS | `follow_redirects=True`; add The Block, Blockworks, Bitcoin Magazine | The CoinDesk URL was never wrong — it answers 308 and serves valid RSS one hop away, but the client did not follow redirects. Same for Blockworks' `.co` → `.com` move. CryptoSlate is excluded: it answers 403 to any non-browser client, browser User-Agent included. |
| GDELT | Narrow the query to crypto terms | Now an efficiency measure, not a correctness one: the gate already cut GDELT from 19.9% to 2.6% of ingested rows in production. A tighter query spends the rate-limited budget on articles that can survive it. |

**Reddit is deferred indefinitely, not scheduled.** Reddit closed self-service
Data API registration in 2026; new OAuth credentials require an approval ticket
with an architecture description and take weeks to months, against unpublished
rejection criteria. Devvit is not an alternative — it builds apps that run
*inside* Reddit, installation requires moderator rights on each target
subreddit, its outbound HTTP is a review-gated premium feature, and its rules
forbid sharing Reddit data to train models or otherwise commercialise it. If
pre-existing credentials turn up, the provider only needs two env vars.

### Added

All three were probed **from inside a collector container on the VPS**, not from
a laptop — The Block taught us that lesson by answering 200 at home and 403 in
production.

| Source | Probe result | Status |
|---|---|---|
| CryptoPanic | `403` without a token (Cloudflare); the `developer/v2` endpoint is `404` | **Dropped — no free tier.** The plan assumed a rate-limited free plan; the operator checked and cryptopanic.com/developers is now paid-only. Out of scope under the free-tier-only constraint. Revisit only if that constraint changes. |
| StockTwits | `403 Just a moment...` — a Cloudflare challenge on every endpoint tried | **Shelved.** The spec said to ship it disabled rather than work around the closure if it refused. It refuses. Getting through means impersonating a browser, which is both a terms violation and the kind of evasion this project will not do. |
| Telegram | not probed | **Blocked on an operator action.** The Bot API only reads channels the bot has joined, and joining a public channel as a reader still requires being added by an admin. Channel selection is a human decision either way. |

Nothing here was half-built: an untested provider written against an unverified
schema is worse than no provider, and guessing at field names is exactly how the
NewsData mis-tagging this whole spec exists to fix got in.

**Net effect on phase 4:** of the three sources planned, two are now unreachable
under a free-tier-only constraint and one waits on channel selection. The
phase's real deliverable turned out to be the `MARKET` regime wiring, which
needed no third party at all.

Live sources are added to `KNOWN_PLATFORMS` (`runtime.py:18`) so they appear in the
terminal's operator toggles and can be muted without a redeploy.

## Data migration

Per the decision above: a one-shot script truncates `raw_content`,
`content_sentiment_agg` and `content_sentiment_agg_daily`, then the collectors
re-populate from scratch under the new rules. This discards a few days of
collection history — accepted, because every stored symbol attribution predates
the fix and the read windows extend to 5 years.

The script lives in `scripts/`, requires an explicit confirmation flag, and prints
the row counts it is about to destroy before doing so.

## Implementation phasing

The work is one coherent goal but too large for a single reviewable step. It
decomposes into four phases, each independently shippable and verifiable:

1. **Normalization core** — `lexicon.py`, `normalize.py`, the CoinGecko Redis
   write, the loop hook, metrics, and the full offline test suite. Nothing else
   changes; the improvement is measurable on ingestion alone.
2. **Data wipe** — the truncation script, run once after phase 1 is deployed and
   observed to behave. Ordering matters: wiping before the filter is live would
   just re-pollute.
3. **Source revival** — Reddit OAuth, the 4chan/Farcaster filter removal,
   CryptoCompare, the RSS feed list, the GDELT query.
4. **New sources** — CryptoPanic, then StockTwits (verify-then-ship-or-disable),
   then Telegram. Plus the `MARKET` wiring in the decision-engine, which is
   independent of 3 and 4 and can land alongside either.

## Testing

Fixtures are **real production rows**, captured from the VPS, not invented:

| Fixture | Expected |
|---|---|
| *"Hyperliquid Price Prediction Today… for HYPE"* (NewsData tags `JST,FLOW,KEEP,BTC,ONE,USDT,NEAR`) | `["HYPE"]` — every uncorroborated tag rejected |
| *"Bitcoin Slides Under $64K…"* (tags `ETH,BTC,ONE,USDT,REKT,BAND,USDC`) | `["BTC"]` |
| A `caughtoffside.com` GDELT football article | dropped, `reason=not_relevant` |
| *"SEC approves new framework"* (no ticker) | `["MARKET"]`, kept |
| A post containing `$ONE` | `["ONE"]` — cashtag confirms an ambiguous ticker |
| A post containing the word "one" | no symbol from it |

Plus unit tests per gate, `SymbolLexicon` construction (including the computed
`ambiguous` set and seed fallback), and a `LexiconLoader` test against a fake
cache. Everything offline — no network, no DB.

Integration: the existing collector tests are extended to assert that the loop
calls the normalizer between `fetch` and `insert_items`.

## How the MARKET regime signal is wired

`MARKET` reaches `Features.market_sentiment` and is folded into `news_score`.
Three constraints are deliberate and should survive any future edit:

- **The symbol's own sentiment always wins.** The regime is a fallback for
  symbols that have none — which is most of them, most of the time — never an
  override of a direct reading.
- **It is damped halfway to neutral** (`_MARKET_DAMPING = 0.5`). A market-wide
  read is real but is not about this token: it nudges a score, it does not
  decide one.
- **It does not count toward confidence.** Confidence measures symbol-specific
  evidence. A market read is identical for every symbol, so counting it would
  lift the entire book at once — precisely what a confidence measure must not do.

The read **expires after an hour**. Without that, a quiet weekend would keep
applying Friday's mood to Monday's decisions, and a collector outage would
freeze the last value in place indefinitely.

The five scoring weights are untouched: the regime rides inside `news_score`
specifically so the tuned model needs no retuning, and a test pins their sum
at 1.0.

## Known issues / corrections to make

None of these block anything currently running.

### 0. RESOLVED — `_norm_news` conflated "no news" with "maximally bearish news"

Fixed by removing the early return, so absence falls through to exactly where a
neutral reading sits. Option A of the three below was chosen.

**Measured before shipping**, by replaying 12,183 real production signals (every
stored signal scoring ≥ 50) through both versions:

| | before | after |
|---|---|---|
| signals with neither news nor sentiment | 43.0% | — |
| score shift on those | — | **+5 points** |
| score shift on the rest | — | 0 |
| decision-engine score, max | 61 | 61 |
| decision-engine score, median | 39 | 40 |
| **crossings of the decision threshold (70)** | — | **0** |

Safe to ship: nine points of headroom remain between the highest achievable
score and the threshold. That headroom is itself a finding — see issue 4.

A first attempt at this measurement used the 30,000 *most recent* signals and
reported reassuring numbers from an unrepresentative sample: that batch was
deterministic triage on small caps, none of it near the threshold. Sample by
score, not by recency, when the question is "does this change any decision".

### 1. `CONTENT_DROPPED` has no denominator

The counter reports items rejected, never items considered, so a rejection
*rate* cannot be read from `/metrics`. Judging whether the gate over-filters
means counting `raw_content` rows per source in the database. A "considered"
counter alongside it would make the ratio directly observable.

### 2. `COMMON_WORDS` needs re-auditing on universe rotation

The ambiguous set is computed, but the word list it intersects is hand-curated,
and a ticker missing from it is believed on sight. Three rounds audited it
against the 50-coin seed and passed; the first audit against a top-200-shaped
universe found `ATH`, `PUMP`, `APE`, `IP`, `AI`, `LAYER` and more. `APR` was
then caught only by inspecting live production rows 90 minutes after deploy.
The method that works is building a lexicon from the real universe and running
plausible headlines through it — not reading the list.

### 3. RESOLVED IN PART — the deterministic path cannot reach its own threshold

**Liquidity is fixed.** `engine.py` now falls back to haiku's own 24h-volume
proxy, mirroring the substitution `ai-worker-haiku`'s scorer has made since
Plan-1. Replayed over the same 12,183 signals:

| | before | after |
|---|---|---|
| signals with a usable liquidity figure | 0.0% | **86.1%** |
| median score | 40 | **54** |
| max score | 61 | **66** |
| mean confidence | 0.557 | **0.686** |
| crossings of the threshold | — | 0 |

**But the path still cannot fire, and the reason is a second dead axis.**
Breaking down the top 100 real signals by contribution:

| axis | weight | best-100 avg | ceiling |
|---|---|---|---|
| `volume_growth` | 0.25 | 24.9 | 25 |
| `social_score` | 0.20 | **0.0** | 20 |
| `news_score` | 0.20 | 16.8 | 20 |
| `market_trend` | 0.20 | 8.1 | 20 |
| `liquidity_score` | 0.15 | 15.0 | 15 |

`social_growth` is populated in **0 of 12,183** signals — another 20 points of
weight permanently at zero. This one is not a key mismatch: nothing in the
system computes it at all. `ai-worker-haiku` reads
`features.get("social_growth")` (`worker.py:196`) but none of its four
extractors ever writes that key, because the producer disappeared when social
ingestion moved from Kafka events to `raw_content` and the consumer was never
updated.

With 20 of 100 points structurally unreachable, clearing 70 demands near-perfect
scores on every remaining axis. The best real signal reaches 66.

Two things to decide, neither done here:

- **Compute `social_growth`.** The data already exists:
  `content_sentiment_agg` holds hourly mention counts per symbol, and
  `SqlSentimentAggReader.window_stats` already aggregates them. Growth is this
  hour's mentions against a trailing baseline. This is new work in haiku, not a
  repair.
- **Re-examine the threshold of 70.** It was presumably chosen when the model
  had five live axes. Whether it still means what it meant then is worth
  checking against a replay before tuning anything.

Found while measuring issue 0, and larger than the issue that surfaced it.

Replaying the 12,183 highest-scoring production signals through
`scoring.score()` gives a **maximum of 61** against a `decision_threshold` of
**70**. Not "rarely fires" — structurally cannot fire on the data it is fed.

The direct cause: `liquidity_usd` was populated in **0 of 12,183** signals.
`engine.py:82` reads `raw.get("liquidity_usd")` from the analysis event's
`meta.features`, but `ai-worker-haiku` only writes that key for DEX-sourced
events (`worker.py:162`). Everything arriving from CoinGecko — nearly the whole
flow — has no such key, so `_norm_liquidity(None)` returns `0.0` and the
`liquidity_score` weight of **0.15 is permanently dead**. Haiku already computes
a volume-based proxy for exactly this case and records `liquidity_source:
"volume_proxy"`; the decision engine simply never reads it.

This corroborates the "score/floor mismatch" recorded in
`memory/pipeline-bottleneck-measured.md` and gives it a precise mechanism.

Deliberately not fixed here: it is another live scoring change and deserves the
same before/after treatment issue 0 got. Two candidate repairs — have the engine
fall back to haiku's volume proxy, or lower the threshold to match what the
model can actually produce. The first is almost certainly right, since the
second would leave 15% of the weight dead and simply move the goalposts.

## Rejected alternatives

**LLM relevance/extraction on the ingestion path (Haiku per item).** Better on
genuinely ambiguous context, but `memory/pipeline-bottleneck-measured.md` records
that production already stalls on the Sonnet budget, and this would add one LLM
call per ingested item on a volume that is about to grow. Non-deterministic and
harder to test. Every false positive actually observed in production (`ONE`,
`JST`, `KEEP`, `FLOW`) is resolvable deterministically, so the LLM would buy
nothing measurable in the first pass.

**Hybrid (deterministic first, LLM arbitrating the residue).** The right eventual
shape, but it should be driven by a measured residual error rate rather than
assumed. `ContentNormalizer` returning a per-candidate confirmation reason leaves
the extension point clean: an arbiter can later be attached to the unconfirmed
set without touching the gates.

**Open-universe discovery.** Maximum recall on early tokens, but close to the
current noise level — rejected in favour of the hybrid universe.
