# Crypto Relevance & Symbol Resolution — Design

**Date:** 2026-07-26
**Status:** Approved (design), pending implementation plan
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

`ambiguous` is **computed**, not hand-maintained: it is the intersection of the
universe's tickers with a bundled common-English-word list. Today that yields
`ONE, CORE, FORM, PEOPLE, KEEP, FLOW, NEAR, BAND, LINK, UNI, GAS, TIME, WIN,
MASK, …` — i.e. exactly the observed false positives. As the universe rotates,
the set updates itself.

**Cold-start fallback:** a seed list of ~50 major coins is bundled in the package.
An empty or unreachable Redis falls back to the seed rather than to an empty
lexicon, so a restart degrades recall instead of silently dropping all content.

### The three gates

`ContentNormalizer.apply` runs each item through, in order:

**Gate 1 — symbol resolution.** Candidates are gathered from four channels:
explicit `$CASHTAG` matches; uppercase ticker tokens on word boundaries; full coin
names from `by_name`; and provider-supplied tags (NewsData `coin`, CryptoCompare
`categories`, CryptoPanic `currencies`). Every candidate — **including
provider-supplied ones** — is then confirmed against the same rules:

| Candidate | Rule |
|---|---|
| In universe, ticker not in `ambiguous` | accept |
| In universe, ticker in `ambiguous` | accept **only** if it arrived as an explicit cashtag, or the coin's full name appears in the text |
| Not in universe | accept **only** if it arrived as an explicit cashtag |

Routing provider tags through the validator is what removes the `ONE`/`JST`
pollution: NewsData's tags survive only where they are corroborated by the text.

**Gate 2 — crypto relevance.** The item is kept if it has at least one confirmed
symbol, **or** matches at least one term from a bundled crypto vocabulary
(blockchain, DeFi, ETF, halving, staking, SEC, stablecoin, exchange, wallet,
altcoin, …). Otherwise it is dropped and never inserted.

**Gate 3 — MARKET fallback.** Relevant but symbol-less items get
`symbols = ["MARKET"]` assigned **at collection time**, so `raw_content` records
the fact explicitly instead of the worker inventing it later. The
`symbols or ["MARKET"]` fallback in `worker.py:67` is removed — the invariant
becomes "every stored row has at least one symbol".

**Observability:** a `CONTENT_DROPPED{source, reason}` Prometheus counter, with
`reason` in `{not_relevant, no_confirmed_symbol}`, exposed on the existing
`/metrics` endpoint. The filter's behaviour is measured, not assumed.

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
| CryptoCompare | Enable | Its `/news` endpoint is free and keyless; `categories` feed Gate 1 as provider tags. |
| RSS | Replace the 308-ing CoinDesk URL; add The Block, Blockworks, CryptoSlate, Bitcoin Magazine | Free, high crypto purity. |
| GDELT | Narrow the query to crypto terms | Gate 2 absorbs the residual noise. |

### Added

| Source | Access | Risk |
|---|---|---|
| CryptoPanic | Free tier, rate-limited | Items carry currency tags and a community bullish/bearish vote — the best free symbol attribution available. |
| StockTwits | Public endpoint | Native `$cashtag` stream. **Much of the public API was closed in 2024.** Implement, verify against the live endpoint; if it refuses, ship it disabled rather than working around the closure. |
| Telegram | Bot API, free | Channel list from env, empty = off. The bot only reads channels it has joined, so channel selection is an operator action. High signal, high spam. |

All three are added to `KNOWN_PLATFORMS` (`runtime.py:18`) so they appear in the
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
