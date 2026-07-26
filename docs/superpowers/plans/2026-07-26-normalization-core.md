# Normalization Core Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop non-crypto content and wrong symbol attributions from ever reaching `raw_content`, by inserting one shared normalization layer at the single point every content provider already passes through.

**Architecture:** A pure, synchronous `ContentNormalizer` applies three gates (symbol resolution → crypto relevance → `MARKET` fallback) against an immutable `SymbolLexicon` built from CoinGecko's top-N coins. All I/O lives in `LexiconLoader` (Redis, in-process cached) and in a thin async adapter. The hook goes into `AdaptivePollLoop.run()` between `fetch()` and `insert_items()`, so no provider is modified and every future provider inherits it.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode = "auto"`), prometheus_client, Redis via `cmi_common.cache.Cache`, ruff + black + mypy strict.

**Spec:** `docs/superpowers/specs/2026-07-26-crypto-relevance-symbol-resolution-design.md`

**Out of scope for this plan** (later phases, separate plans): the data wipe script (phase 2), source revival — Reddit OAuth, 4chan/Farcaster filter removal, CryptoCompare, RSS feeds, GDELT query (phase 3), new sources and the `MARKET`→decision-engine wiring (phase 4).

---

## Background you need

**How ingestion works today.** Each platform (bluesky, reddit, gdelt, …) is a `Provider`: an object with `name`, `kind` (`"social"`/`"news"`), `rate_limit`, and `async fetch() -> list[RawItem]`. Each provider is driven by its own `AdaptivePollLoop` (`libs/cmi_common/cmi_common/sources/loop.py`), which polls, persists via a repository, and sleeps. `collector-social` and `collector-news` each build a list of providers and one loop per provider.

**Why this plan exists.** Providers currently decide symbols themselves, inconsistently: some copy the upstream API's coin tags verbatim (wrong — an article about Hyperliquid gets stored as `JST, FLOW, KEEP, ONE, …`), some require an explicit `$TICKER` cashtag and therefore store nothing at all. Nothing anywhere checks that the content is about crypto.

**The monorepo layout.** Shared code lives in `libs/cmi_common/cmi_common/`. Tests live in the top-level `tests/` directory and import from `cmi_common`. Run tests with `pytest` from the repo root. `make lint` runs ruff + black --check + mypy; mypy is **strict**, so every function needs annotations.

---

## File Structure

| File | Responsibility |
|---|---|
| `libs/cmi_common/cmi_common/sources/vocab.py` (create) | Bundled static word lists only — no logic. Common English words, crypto vocabulary, seed coin list. |
| `libs/cmi_common/cmi_common/sources/lexicon.py` (create) | `SymbolLexicon` (immutable universe snapshot + resolution) and `LexiconLoader` (Redis-backed, in-process cached). |
| `libs/cmi_common/cmi_common/sources/normalize.py` (create) | `ContentNormalizer` (pure, the three gates) + `LexiconNormalizer` (async adapter doing metrics). |
| `libs/cmi_common/cmi_common/observability/metrics.py` (modify) | Add the `CONTENT_DROPPED` counter. |
| `libs/cmi_common/cmi_common/sources/loop.py` (modify) | Call the normalizer between fetch and insert. |
| `libs/cmi_common/cmi_common/sources/__init__.py` (modify) | Re-export the new public names. |
| `services/collector-coingecko/app/application/collector.py` (modify) | Publish the lexicon to Redis each poll. |
| `services/collector-coingecko/app/main.py` (modify) | Pass the `Cache` to the collector. |
| `services/collector-social/app/main.py`, `services/collector-news/app/main.py` (modify) | Build a `LexiconNormalizer` and hand it to every loop. |
| `services/sentiment-service/app/worker.py` (modify) | Drop the `or ["MARKET"]` fallback — the invariant now holds upstream. |

Splitting `vocab.py` out of `lexicon.py` keeps a few hundred lines of static word data from burying ~80 lines of logic. Splitting the pure `ContentNormalizer` from the I/O-doing `LexiconNormalizer` is what makes the whole rule set testable with no Redis, no DB and no network.

---

### Task 1: `SymbolLexicon` — the universe snapshot

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/vocab.py`
- Create: `libs/cmi_common/cmi_common/sources/lexicon.py`
- Test: `tests/test_symbol_lexicon.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_symbol_lexicon.py`:

```python
"""SymbolLexicon: universe snapshot, ticker/name resolution, computed ambiguity."""

from __future__ import annotations

from cmi_common.sources import SymbolLexicon

COINS = [
    {"ticker": "BTC", "name": "Bitcoin"},
    {"ticker": "ETH", "name": "Ethereum"},
    {"ticker": "HYPE", "name": "Hyperliquid"},
    {"ticker": "ONE", "name": "Harmony"},
    {"ticker": "KEEP", "name": "Keep Network"},
]


def test_resolves_ticker_case_insensitively() -> None:
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.resolve_ticker("btc") == "BTC"
    assert lex.resolve_ticker("BTC") == "BTC"


def test_unknown_ticker_resolves_to_none() -> None:
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.resolve_ticker("NOTACOIN") is None


def test_resolves_full_coin_names_found_in_text() -> None:
    lex = SymbolLexicon.from_coins(COINS)
    found = lex.names_in("hyperliquid price prediction today for hype")
    assert found == {"HYPE"}


def test_name_matching_respects_word_boundaries() -> None:
    # "bitcoiner" must not match the coin name "Bitcoin".
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.names_in("every bitcoiner knows") == set()


def test_ambiguous_set_is_computed_from_common_words() -> None:
    # ONE and KEEP are ordinary English words; BTC/ETH/HYPE are not.
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.is_ambiguous("ONE") is True
    assert lex.is_ambiguous("KEEP") is True
    assert lex.is_ambiguous("BTC") is False
    assert lex.is_ambiguous("HYPE") is False


def test_seed_lexicon_is_non_empty_and_knows_majors() -> None:
    # Guarantees a cold Redis degrades recall instead of dropping everything.
    from cmi_common.sources import SEED_LEXICON

    assert SEED_LEXICON.resolve_ticker("BTC") == "BTC"
    assert SEED_LEXICON.resolve_ticker("ETH") == "ETH"


def test_blank_and_short_names_are_ignored() -> None:
    # A 2-char coin name would match half the corpus; it must not be indexed.
    lex = SymbolLexicon.from_coins([{"ticker": "OK", "name": "Ok"}, *COINS])
    assert lex.names_in("it is ok to buy") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_symbol_lexicon.py -v`
Expected: FAIL — `ImportError: cannot import name 'SymbolLexicon' from 'cmi_common.sources'`

- [ ] **Step 3: Write the static vocabulary**

Create `libs/cmi_common/cmi_common/sources/vocab.py`:

```python
"""Static word lists backing the lexicon and the relevance gate.

Data only — no logic. Kept apart from ``lexicon.py`` so the resolution rules
stay readable.
"""

from __future__ import annotations

# Ordinary English words that are also crypto tickers. Intersecting this with
# the live universe yields the "ambiguous" set: tickers that must be corroborated
# before they are believed. Uppercase, 2-6 chars (the ticker shape).
COMMON_WORDS: frozenset[str] = frozenset(
    """
    ONE TWO SIX TEN ALL AND ANY ARE ASK BAD BAG BAN BAND BANK BASE BEAR BEST BET
    BID BIG BIT BLUE BODY BOND BOOK BOOM BOOST BOT BOX BOY BULL BUY CALL CAN CAP
    CAR CARD CARE CASE CASH CAT CELL CHAT CITY CLUB COIN COLD COME CORE COST
    COVER CUT DATA DAY DEAL DEEP DOG DONE DOOR DOWN DRAW DROP DUE EACH EARN EAST
    EASY EDGE END EVEN EVER EYE FACE FACT FAIR FALL FAN FAR FAST FEE FEEL FEW
    FILE FILL FILM FIND FIRE FIRM FISH FIT FIVE FIX FLOW FLY FOOD FOOT FOR FORM
    FOUR FREE FROM FUEL FULL FUN FUND GAIN GAME GAS GATE GET GIFT GIVE GLOW GO
    GOAL GOLD GOOD GRID GROW HALF HAND HARD HAS HAT HAVE HEAD HEAR HEAT HELP
    HERE HIGH HILL HIT HOLD HOME HOPE HOT HOUR HOW HUB HUGE ICE ICON IDEA INCH
    INTO IRON ITEM JOB JOIN JUMP JUST KEEP KEY KID KIND KING KNOW LAB LAND LAST
    LATE LAW LAY LEAD LEAF LEAN LEFT LEG LESS LET LIFE LIFT LIGHT LIKE LINE LINK
    LIST LIVE LOAD LOAN LOCK LOG LONG LOOK LOSS LOT LOVE LOW LUCK MAIL MAIN MAKE
    MAN MANY MAP MARK MASK MASS MEAL MEAN MEET MEME MEN MESH MILE MILK MIND MINE
    MINT MISS MIX MODE MOON MORE MOST MOVE MUCH MUST NAME NEAR NECK NEED NET NEW
    NEWS NEXT NICE NINE NODE NONE NOON NORM NOSE NOT NOTE NOW NUT OFF OIL OLD ON
    ONCE ONLY OPEN OR ORDER OUR OUT OVER OWN PACE PACK PAGE PAID PAIN PAIR PAPER
    PARK PART PASS PAST PATH PAY PEAK PEN PEOPLE PET PICK PIE PIN PIPE PLAN PLAY
    PLUS POINT POOL POOR POP PORT POST POUR POWER PRESS PULL PUSH PUT RACE RAIN
    RANK RARE RATE RAW REACH READ REAL RED RENT REST RICH RIDE RING RISE RISK
    ROAD ROCK ROLE ROLL ROOM ROOT ROSE RULE RUN SAFE SAID SAIL SALE SALT SAME
    SAND SAVE SAY SEA SEAT SEE SEED SELF SELL SEND SET SHIP SHOP SHOT SHOW SIDE
    SIGN SILK SIT SITE SIZE SKIN SKY SLIP SLOW SNAP SNOW SOFT SOIL SOLD SOLE
    SOME SON SONG SOON SORT SOUL SOUP SPOT STAR STAY STEP STOP SUM SUN SURE SWAP
    TAG TAKE TALK TALL TAPE TASK TAX TEAM TECH TELL TEN TERM TEST TEXT THAN THAT
    THE THEM THEN THEY THIN THIS THUS TIDE TIE TILE TIME TINY TIP TOOL TOP TOUR
    TOWN TOY TRACK TRADE TRAIL TRAIN TREE TRIP TRUE TRUST TRY TURN TWIN TYPE
    UNIT UP US USE USER VAN VAST VERY VIEW VOTE WAIT WAKE WALK WALL WANT WAR
    WARM WASH WAVE WAY WEAK WEAR WEB WEEK WELL WENT WEST WET WHAT WHEN WHO WHY
    WIDE WIFE WILD WILL WIN WIND WINE WING WIRE WISE WISH WITH WOLF WOOD WORD
    WORK WORLD WORTH YARD YEAR YES YET YOU YOUR ZERO ZONE
    """.split()
)

# Vocabulary that makes an item crypto-relevant even with no ticker in sight.
# Lowercase; matched on word boundaries against title + body.
CRYPTO_KEYWORDS: frozenset[str] = frozenset(
    """
    airdrop altcoin bitcoin blockchain bridge cbdc cex coinbase crypto
    cryptocurrency custody dao defi depeg derivatives dex etf exchange
    futures gas halving hodl kraken layer2 ledger leverage liquidation
    liquidity mempool mining nft onchain perpetual rollup rugpull sec
    seedphrase sec-filing selfcustody smartcontract solidity stablecoin
    staking stakers tokenomics tvl validator wallet web3 whale zk zkproof
    binance tether ethereum
    """.split()
)

# Cold-start fallback: an unreachable or empty Redis must degrade recall, not
# blank the lexicon and drop every item. (ticker, name) pairs.
SEED_COINS: tuple[tuple[str, str], ...] = (
    ("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("USDT", "Tether"),
    ("BNB", "BNB"), ("SOL", "Solana"), ("USDC", "USD Coin"),
    ("XRP", "XRP"), ("DOGE", "Dogecoin"), ("ADA", "Cardano"),
    ("TRX", "TRON"), ("AVAX", "Avalanche"), ("SHIB", "Shiba Inu"),
    ("DOT", "Polkadot"), ("LINK", "Chainlink"), ("BCH", "Bitcoin Cash"),
    ("NEAR", "NEAR Protocol"), ("MATIC", "Polygon"), ("LTC", "Litecoin"),
    ("ICP", "Internet Computer"), ("UNI", "Uniswap"), ("APT", "Aptos"),
    ("XLM", "Stellar"), ("ETC", "Ethereum Classic"), ("ATOM", "Cosmos"),
    ("HBAR", "Hedera"), ("FIL", "Filecoin"), ("ARB", "Arbitrum"),
    ("VET", "VeChain"), ("OP", "Optimism"), ("MKR", "Maker"),
    ("INJ", "Injective"), ("SUI", "Sui"), ("GRT", "The Graph"),
    ("AAVE", "Aave"), ("RUNE", "THORChain"), ("ALGO", "Algorand"),
    ("SEI", "Sei"), ("TIA", "Celestia"), ("PEPE", "Pepe"),
    ("HYPE", "Hyperliquid"), ("TON", "Toncoin"), ("STX", "Stacks"),
    ("IMX", "Immutable"), ("RNDR", "Render"), ("FTM", "Fantom"),
    ("EGLD", "MultiversX"), ("SAND", "The Sandbox"), ("AXS", "Axie Infinity"),
    ("THETA", "Theta Network"), ("FLOW", "Flow"),
)
```

- [ ] **Step 4: Write `SymbolLexicon`**

Create `libs/cmi_common/cmi_common/sources/lexicon.py`:

```python
"""The token universe: what counts as a symbol, and which tickers lie.

``SymbolLexicon`` is an immutable snapshot built from CoinGecko's top-N coins.
It answers three questions and nothing else: does this token resolve to a coin,
which coin names appear in this text, and is this ticker one of the homographs
(ONE, KEEP, FLOW...) that must be corroborated before it is believed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .vocab import COMMON_WORDS, SEED_COINS

# A coin name shorter than this matches far too much ordinary prose to index.
_MIN_NAME_LEN = 4


@dataclass(frozen=True, slots=True)
class SymbolLexicon:
    by_ticker: Mapping[str, str]
    by_name: Mapping[str, str]
    ambiguous: frozenset[str]
    _name_re: re.Pattern[str] | None

    @classmethod
    def from_coins(cls, coins: Iterable[Mapping[str, str]]) -> SymbolLexicon:
        """Build a snapshot from ``[{"ticker": "BTC", "name": "Bitcoin"}, ...]``."""
        by_ticker: dict[str, str] = {}
        by_name: dict[str, str] = {}
        for coin in coins:
            ticker = (coin.get("ticker") or "").strip().upper()
            name = (coin.get("name") or "").strip().lower()
            if not ticker:
                continue
            by_ticker[ticker] = ticker
            if len(name) >= _MIN_NAME_LEN:
                by_name[name] = ticker
        ambiguous = frozenset(t for t in by_ticker if t in COMMON_WORDS)
        # One alternation over all names beats N searches per item. Longest
        # first so "bitcoin cash" wins over "bitcoin".
        names = sorted(by_name, key=len, reverse=True)
        pattern = (
            re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
            if names
            else None
        )
        return cls(by_ticker, by_name, ambiguous, pattern)

    def resolve_ticker(self, token: str) -> str | None:
        """Canonical symbol for a ticker-shaped token, or None if out of universe."""
        return self.by_ticker.get(token.strip().upper())

    def names_in(self, lowered_text: str) -> set[str]:
        """Symbols whose full coin name appears in `lowered_text`."""
        if self._name_re is None:
            return set()
        return {self.by_name[m] for m in self._name_re.findall(lowered_text)}

    def is_ambiguous(self, symbol: str) -> bool:
        """True when the ticker is also an ordinary English word."""
        return symbol in self.ambiguous


SEED_LEXICON = SymbolLexicon.from_coins(
    [{"ticker": t, "name": n} for t, n in SEED_COINS]
)
```

- [ ] **Step 5: Export from the package**

In `libs/cmi_common/cmi_common/sources/__init__.py`, add the import and the `__all__` entries (keep both lists alphabetical, as they are today):

```python
from .lexicon import SEED_LEXICON, SymbolLexicon
```

Add `"SEED_LEXICON"` and `"SymbolLexicon"` to `__all__`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_symbol_lexicon.py -v`
Expected: 7 passed

- [ ] **Step 7: Lint**

Run: `make lint`
Expected: no errors. (If mypy complains about the `_name_re` leading underscore in a dataclass field, that is expected to pass — dataclass fields may start with `_`; they are just positional in `__init__`.)

- [ ] **Step 8: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/vocab.py \
        libs/cmi_common/cmi_common/sources/lexicon.py \
        libs/cmi_common/cmi_common/sources/__init__.py \
        tests/test_symbol_lexicon.py
git commit -m "feat(sources): SymbolLexicon with computed homograph set"
```

---

### Task 2: `LexiconLoader` — Redis-backed, cached, seed-fallback

**Files:**
- Modify: `libs/cmi_common/cmi_common/sources/lexicon.py`
- Test: `tests/test_lexicon_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lexicon_loader.py`:

```python
"""LexiconLoader: Redis read, in-process caching, seed fallback."""

from __future__ import annotations

from typing import Any

from cmi_common.sources import LEXICON_KEY, LexiconLoader


class FakeCache:
    """Minimal Cache stand-in; counts reads so caching is observable."""

    def __init__(self, value: Any = None, *, raises: bool = False) -> None:
        self.value = value
        self.reads = 0
        self._raises = raises

    async def get_json(self, key: str) -> Any:
        assert key == LEXICON_KEY
        self.reads += 1
        if self._raises:
            raise RuntimeError("redis down")
        return self.value


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_builds_lexicon_from_cached_coins() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    loader = LexiconLoader(cache, clock=Clock())
    lex = await loader.get()
    assert lex.resolve_ticker("HYPE") == "HYPE"


async def test_second_call_within_refresh_window_does_not_reread() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=Clock())
    await loader.get()
    await loader.get()
    assert cache.reads == 1


async def test_rereads_after_refresh_window_elapses() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    clock = Clock()
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=clock)
    await loader.get()
    clock.now = 901.0
    await loader.get()
    assert cache.reads == 2


async def test_empty_redis_falls_back_to_seed() -> None:
    loader = LexiconLoader(FakeCache(None), clock=Clock())
    lex = await loader.get()
    assert lex.resolve_ticker("BTC") == "BTC"


async def test_redis_failure_falls_back_to_seed_instead_of_raising() -> None:
    # A Redis blip must degrade recall, never take the collector's loop down.
    loader = LexiconLoader(FakeCache(raises=True), clock=Clock())
    lex = await loader.get()
    assert lex.resolve_ticker("BTC") == "BTC"


async def test_keeps_last_good_lexicon_when_a_later_refresh_fails() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    clock = Clock()
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=clock)
    await loader.get()
    cache._raises = True
    clock.now = 901.0
    lex = await loader.get()
    assert lex.resolve_ticker("HYPE") == "HYPE"  # not downgraded to seed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lexicon_loader.py -v`
Expected: FAIL — `ImportError: cannot import name 'LEXICON_KEY' from 'cmi_common.sources'`

- [ ] **Step 3: Implement `LexiconLoader`**

First replace the import block at the top of `libs/cmi_common/cmi_common/sources/lexicon.py` with:

```python
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .vocab import COMMON_WORDS, SEED_COINS
```

Then append to the same file:

```python
logger = logging.getLogger(__name__)

#: Redis key written by collector-coingecko, read by every content collector.
LEXICON_KEY = "lexicon:coins"


class _CacheLike(Protocol):
    async def get_json(self, key: str) -> Any: ...


class LexiconLoader:
    """Serves the current lexicon, re-reading Redis at most every N seconds.

    Failure policy is deliberate: never raise. A cold or broken Redis yields the
    bundled seed lexicon, and a refresh that fails keeps the last good snapshot.
    Losing the lexicon would make the relevance gate drop everything, so
    degrading recall always beats propagating the error.
    """

    def __init__(
        self,
        cache: _CacheLike,
        *,
        refresh_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache = cache
        self._refresh = refresh_seconds
        self._clock = clock
        self._lexicon: SymbolLexicon | None = None
        self._loaded_at = 0.0

    async def get(self) -> SymbolLexicon:
        now = self._clock()
        if self._lexicon is not None and now - self._loaded_at < self._refresh:
            return self._lexicon
        try:
            coins = await self._cache.get_json(LEXICON_KEY)
        except Exception:
            logger.warning("lexicon read failed; keeping previous", exc_info=True)
            coins = None
        if coins:
            self._lexicon = SymbolLexicon.from_coins(coins)
            self._loaded_at = now
        elif self._lexicon is None:
            self._lexicon = SEED_LEXICON
            self._loaded_at = now
        return self._lexicon
```

- [ ] **Step 4: Export from the package**

In `libs/cmi_common/cmi_common/sources/__init__.py`, extend the lexicon import and `__all__`:

```python
from .lexicon import LEXICON_KEY, SEED_LEXICON, LexiconLoader, SymbolLexicon
```

Add `"LEXICON_KEY"` and `"LexiconLoader"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_lexicon_loader.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/lexicon.py \
        libs/cmi_common/cmi_common/sources/__init__.py \
        tests/test_lexicon_loader.py
git commit -m "feat(sources): LexiconLoader with seed fallback and last-good retention"
```

---

### Task 3: `ContentNormalizer` — the three gates

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/normalize.py`
- Test: `tests/test_content_normalizer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_content_normalizer.py`:

```python
"""ContentNormalizer: symbol resolution, relevance gate, MARKET fallback."""

from __future__ import annotations

from cmi_common.sources import ContentNormalizer, RawItem, SymbolLexicon

COINS = [
    {"ticker": "BTC", "name": "Bitcoin"},
    {"ticker": "ETH", "name": "Ethereum"},
    {"ticker": "HYPE", "name": "Hyperliquid"},
    {"ticker": "ONE", "name": "Harmony"},
    {"ticker": "KEEP", "name": "Keep Network"},
    {"ticker": "USDT", "name": "Tether"},
]
LEX = SymbolLexicon.from_coins(COINS)


def _item(title: str = "", text: str = "", **kw: object) -> RawItem:
    return RawItem(
        source=str(kw.pop("source", "stub")),
        kind="news",
        external_id=str(kw.pop("external_id", "1")),
        title=title,
        text=text,
        symbols=list(kw.pop("symbols", []) or []),
    )


def _norm() -> ContentNormalizer:
    return ContentNormalizer(LEX)


def test_unambiguous_ticker_in_text_is_accepted() -> None:
    result = _norm().apply([_item(title="BTC breaks out")])
    assert result.kept[0].symbols == ["BTC"]


def test_coin_name_in_text_resolves_to_its_ticker() -> None:
    result = _norm().apply([_item(title="Ethereum upgrade ships")])
    assert result.kept[0].symbols == ["ETH"]


def test_ambiguous_ticker_without_corroboration_is_rejected() -> None:
    # "ONE" the English word must not become the Harmony token.
    result = _norm().apply([_item(title="ONE more bitcoin rally")])
    assert result.kept[0].symbols == ["BTC"]


def test_ambiguous_ticker_with_cashtag_is_accepted() -> None:
    result = _norm().apply([_item(title="$ONE is pumping")])
    assert result.kept[0].symbols == ["ONE"]


def test_ambiguous_ticker_with_full_name_is_accepted() -> None:
    result = _norm().apply([_item(title="Harmony ONE network update")])
    assert result.kept[0].symbols == ["ONE"]


def test_out_of_universe_token_needs_a_cashtag() -> None:
    result = _norm().apply([_item(title="WIF season", text="crypto is back")])
    assert result.kept[0].symbols == ["MARKET"]
    result2 = _norm().apply([_item(title="$WIF season")])
    assert result2.kept[0].symbols == ["WIF"]


def test_provider_supplied_symbols_are_discarded() -> None:
    # The exact production failure: NewsData tagged a Bitcoin article with
    # coins that appear nowhere in it. Only BTC, from the word "Bitcoin".
    item = _item(
        title="Bitcoin Slides Under $64K Amid Rising Treasury Yields",
        symbols=["ETH", "BTC", "ONE", "USDT", "REKT", "BAND", "USDC"],
    )
    assert _norm().apply([item]).kept[0].symbols == ["BTC"]


def test_non_crypto_content_is_dropped() -> None:
    result = _norm().apply([_item(title="Local football club wins final")])
    assert result.kept == []
    assert result.dropped[0][1] == "not_relevant"


def test_crypto_content_without_symbol_becomes_market() -> None:
    item = _item(title="SEC approves a new regulatory framework for exchanges")
    assert _norm().apply([item]).kept[0].symbols == ["MARKET"]


def test_empty_text_is_dropped_with_its_own_reason() -> None:
    result = _norm().apply([_item()])
    assert result.kept == []
    assert result.dropped[0][1] == "empty_text"


def test_symbols_are_sorted_and_deduplicated() -> None:
    item = _item(title="Ethereum and Bitcoin and BTC again")
    assert _norm().apply([item]).kept[0].symbols == ["BTC", "ETH"]


def test_original_item_is_not_mutated() -> None:
    item = _item(title="Bitcoin rallies", symbols=["ONE"])
    _norm().apply([item])
    assert item.symbols == ["ONE"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_content_normalizer.py -v`
Expected: FAIL — `ImportError: cannot import name 'ContentNormalizer' from 'cmi_common.sources'`

- [ ] **Step 3: Implement the pure normalizer**

Create `libs/cmi_common/cmi_common/sources/normalize.py`:

```python
"""Crypto relevance gate + symbol resolution, applied to every ingested item.

``ContentNormalizer`` is pure and synchronous: same lexicon + same items always
gives the same result, with no I/O. That is what lets the whole rule set be
tested against real production rows offline. The async adapter that reads the
lexicon and emits metrics is ``LexiconNormalizer``.

Symbols are derived from the item's own text only. Provider-supplied tags are
discarded rather than validated: validating one could only mean "the ticker or
coin name occurs in the text", which is exactly what this computes anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lexicon import SymbolLexicon
from .raw import RawItem
from .vocab import CRYPTO_KEYWORDS

#: Symbol carrying crypto-relevant content that names no specific coin.
MARKET_SYMBOL = "MARKET"

DROP_NOT_RELEVANT = "not_relevant"
DROP_EMPTY_TEXT = "empty_text"

_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
_UPPER_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\b")
_KEYWORD = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, CRYPTO_KEYWORDS), key=len, reverse=True)) + r")\b"
)


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    kept: list[RawItem]
    #: (item, reason) for everything rejected, so the caller can count reasons.
    dropped: list[tuple[RawItem, str]]


class ContentNormalizer:
    def __init__(self, lexicon: SymbolLexicon) -> None:
        self._lex = lexicon

    def apply(self, items: list[RawItem]) -> NormalizeResult:
        kept: list[RawItem] = []
        dropped: list[tuple[RawItem, str]] = []
        for item in items:
            text = f"{item.title or ''} {item.text or ''}".strip()
            if not text:
                dropped.append((item, DROP_EMPTY_TEXT))
                continue
            symbols = self._resolve(text)
            if not symbols:
                if not _KEYWORD.search(text.lower()):
                    dropped.append((item, DROP_NOT_RELEVANT))
                    continue
                symbols = [MARKET_SYMBOL]
            kept.append(item.model_copy(update={"symbols": symbols}))
        return NormalizeResult(kept=kept, dropped=dropped)

    def _resolve(self, text: str) -> list[str]:
        lowered = text.lower()
        named = self._lex.names_in(lowered)
        confirmed: set[str] = set(named)

        # A cashtag is an explicit claim by the author, so it confirms an
        # ambiguous ticker and is the only way an out-of-universe token gets in.
        for raw in _CASHTAG.findall(text):
            token = raw.upper()
            confirmed.add(self._lex.resolve_ticker(token) or token)

        # A bare uppercase token is believed only if it is in the universe and
        # is not an ordinary English word that happens to be a ticker.
        for token in _UPPER_TOKEN.findall(text):
            symbol = self._lex.resolve_ticker(token)
            if symbol is None or (self._lex.is_ambiguous(symbol) and symbol not in named):
                continue
            confirmed.add(symbol)

        return sorted(confirmed)
```

- [ ] **Step 4: Export from the package**

In `libs/cmi_common/cmi_common/sources/__init__.py`:

```python
from .normalize import (
    MARKET_SYMBOL,
    ContentNormalizer,
    NormalizeResult,
)
```

Add `"MARKET_SYMBOL"`, `"ContentNormalizer"`, `"NormalizeResult"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_content_normalizer.py -v`
Expected: 12 passed

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add libs/cmi_common/cmi_common/sources/normalize.py \
        libs/cmi_common/cmi_common/sources/__init__.py \
        tests/test_content_normalizer.py
git commit -m "feat(sources): ContentNormalizer relevance gate and symbol resolution"
```

---

### Task 4: `CONTENT_DROPPED` metric + `LexiconNormalizer` adapter

**Files:**
- Modify: `libs/cmi_common/cmi_common/observability/metrics.py`
- Modify: `libs/cmi_common/cmi_common/observability/__init__.py`
- Modify: `libs/cmi_common/cmi_common/sources/normalize.py`
- Test: `tests/test_lexicon_normalizer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lexicon_normalizer.py`:

```python
"""LexiconNormalizer: async adapter over the pure normalizer, plus drop metrics."""

from __future__ import annotations

from cmi_common.observability import CONTENT_DROPPED
from cmi_common.sources import LexiconLoader, LexiconNormalizer, RawItem


class FakeCache:
    async def get_json(self, key: str) -> object:
        return [{"ticker": "BTC", "name": "Bitcoin"}]


def _item(title: str, external_id: str = "1") -> RawItem:
    return RawItem(source="stub", kind="news", external_id=external_id, title=title)


async def test_returns_only_kept_items_with_resolved_symbols() -> None:
    norm = LexiconNormalizer(LexiconLoader(FakeCache()), service="collector-news")
    out = await norm.normalize(
        [_item("Bitcoin rallies", "1"), _item("Local football final", "2")]
    )
    assert [i.external_id for i in out] == ["1"]
    assert out[0].symbols == ["BTC"]


async def test_increments_the_drop_counter_with_source_and_reason() -> None:
    norm = LexiconNormalizer(LexiconLoader(FakeCache()), service="collector-news")
    before = CONTENT_DROPPED.labels("collector-news", "stub", "not_relevant")._value.get()
    await norm.normalize([_item("Local football final", "2")])
    after = CONTENT_DROPPED.labels("collector-news", "stub", "not_relevant")._value.get()
    assert after == before + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lexicon_normalizer.py -v`
Expected: FAIL — `ImportError: cannot import name 'CONTENT_DROPPED' from 'cmi_common.observability'`

- [ ] **Step 3: Add the metric**

In `libs/cmi_common/cmi_common/observability/metrics.py`, after the `UPSTREAM_REQUESTS` definition:

```python
CONTENT_DROPPED = Counter(
    "cmi_content_dropped_total",
    "Ingested items rejected by the crypto relevance gate",
    ["service", "source", "reason"],
)
```

In `libs/cmi_common/cmi_common/observability/__init__.py`, add `CONTENT_DROPPED` to the imports from `.metrics` and to `__all__`, keeping both alphabetical.

- [ ] **Step 4: Implement the adapter**

Append to `libs/cmi_common/cmi_common/sources/normalize.py` (add `from ..observability import CONTENT_DROPPED` and `from .lexicon import LexiconLoader, SymbolLexicon` to the imports):

```python
class LexiconNormalizer:
    """Async seam between the poll loop and the pure normalizer.

    Owns the two things ``ContentNormalizer`` deliberately refuses: fetching the
    current lexicon, and emitting metrics.
    """

    def __init__(self, loader: LexiconLoader, *, service: str) -> None:
        self._loader = loader
        self._service = service

    async def normalize(self, items: list[RawItem]) -> list[RawItem]:
        lexicon = await self._loader.get()
        result = ContentNormalizer(lexicon).apply(items)
        for item, reason in result.dropped:
            CONTENT_DROPPED.labels(self._service, item.source, reason).inc()
        return result.kept
```

- [ ] **Step 5: Export from the package**

In `libs/cmi_common/cmi_common/sources/__init__.py`, add `LexiconNormalizer` to the `.normalize` import and to `__all__`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_lexicon_normalizer.py -v`
Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/observability/ \
        libs/cmi_common/cmi_common/sources/normalize.py \
        libs/cmi_common/cmi_common/sources/__init__.py \
        tests/test_lexicon_normalizer.py
git commit -m "feat(obs): CONTENT_DROPPED counter and LexiconNormalizer adapter"
```

---

### Task 5: Hook the normalizer into `AdaptivePollLoop`

**Files:**
- Modify: `libs/cmi_common/cmi_common/sources/loop.py:29-84`
- Test: `tests/test_adaptive_poll_loop.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adaptive_poll_loop.py`:

```python
class DroppingNormalizer:
    """Normalizer stand-in: keeps nothing, records what it was handed."""

    def __init__(self) -> None:
        self.seen: list[RawItem] = []

    async def normalize(self, items: list[RawItem]) -> list[RawItem]:
        self.seen.extend(items)
        return []


async def test_normalizer_runs_between_fetch_and_persist() -> None:
    repo = FakeContentRepository()
    item = RawItem(source="stub", kind="social", external_id="1")
    provider = StubProvider(items=[item])
    normalizer = DroppingNormalizer()
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(), poll_interval=300,
                            service="collector-social", sleep=sleeps,
                            normalizer=normalizer)
    await _run(loop)
    assert normalizer.seen == [item]   # it saw the fetched item
    assert repo.rows == []             # and its rejection reached the repository


async def test_loop_without_a_normalizer_persists_unchanged() -> None:
    # The hook is optional so existing wiring keeps working untouched.
    repo = FakeContentRepository()
    provider = StubProvider(items=[RawItem(source="stub", kind="social", external_id="1")])
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert len(repo.rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adaptive_poll_loop.py -v`
Expected: FAIL — `TypeError: AdaptivePollLoop.__init__() got an unexpected keyword argument 'normalizer'`

- [ ] **Step 3: Add the hook**

In `libs/cmi_common/cmi_common/sources/loop.py`, add a protocol above the class:

```python
class Normalizer(Protocol):
    async def normalize(self, items: list[RawItem]) -> list[RawItem]: ...
```

Add `from typing import Protocol` and `from .raw import RawItem` to the imports.

Add the constructor parameter (after `sleep`):

```python
        normalizer: Normalizer | None = None,
```

and, in the body, `self._normalizer = normalizer`.

Then in `run()`, replace:

```python
                items = await self._provider.fetch()
                # Persist inside the try so a transient DB failure backs off
                # like any other error instead of silently killing this loop.
                inserted = await self._repo.insert_items(items)
```

with:

```python
                items = await self._provider.fetch()
                if self._normalizer is not None:
                    # Crypto relevance + symbol resolution, before anything is
                    # stored. One choke point for every provider.
                    items = await self._normalizer.normalize(items)
                # Persist inside the try so a transient DB failure backs off
                # like any other error instead of silently killing this loop.
                inserted = await self._repo.insert_items(items)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adaptive_poll_loop.py -v`
Expected: 6 passed (4 pre-existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/loop.py tests/test_adaptive_poll_loop.py
git commit -m "feat(sources): normalize items between fetch and persist"
```

---

### Task 6: Publish the lexicon from collector-coingecko

**Files:**
- Modify: `services/collector-coingecko/app/application/collector.py`
- Modify: `services/collector-coingecko/app/main.py:27-28`
- Test: `tests/test_coingecko_lexicon_publish.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_coingecko_lexicon_publish.py`:

```python
"""collector-coingecko publishes the token universe for the content collectors."""

from __future__ import annotations

from typing import Any

from cmi_common.sources import LEXICON_KEY

from service_modules import load_service_module

# Every service ships a package literally named `app`; loading two bare would
# shadow each other, so tests/conftest.py asserts nobody does. Always go through
# load_service_module (see tests/service_modules.py for why).
CoinGeckoCollector = load_service_module(
    "collector-coingecko", "application.collector"
).CoinGeckoCollector


class FakeClient:
    async def trending(self) -> list[str]:
        return []

    async def markets(self, per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        if page > 1:
            return []
        return [
            {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
             "current_price": 60000.0, "total_volume": 1.0, "market_cap": 1.0},
        ]


class FakeProducer:
    async def publish(self, topic: Any, event: Any) -> None:
        return None


class FakeCache:
    def __init__(self) -> None:
        self.written: dict[str, Any] = {}

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.written[key] = value


async def test_poll_writes_ticker_and_name_pairs_to_the_lexicon_key() -> None:
    cache = FakeCache()
    collector = CoinGeckoCollector(FakeClient(), FakeProducer(), cache=cache, pages=1)
    await collector.poll_once()
    assert cache.written[LEXICON_KEY] == [{"ticker": "BTC", "name": "Bitcoin"}]


async def test_lexicon_write_failure_does_not_break_the_poll() -> None:
    class BrokenCache(FakeCache):
        async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
            raise RuntimeError("redis down")

    collector = CoinGeckoCollector(FakeClient(), FakeProducer(), cache=BrokenCache(), pages=1)
    published = await collector.poll_once()
    assert published >= 1  # price events still went out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coingecko_lexicon_publish.py -v`
Expected: FAIL — `TypeError: CoinGeckoCollector.__init__() got an unexpected keyword argument 'cache'`

- [ ] **Step 3: Implement the publication**

In `services/collector-coingecko/app/application/collector.py`, add imports:

```python
from cmi_common.cache import Cache
from cmi_common.sources import LEXICON_KEY
```

Add `cache: Cache` as a keyword-only constructor parameter and store it as `self._cache`.

Collect the universe while iterating markets. In `poll_once`, declare `universe: list[dict[str, str]] = []` next to `published = 0`, and inside the `for row in rows:` loop, before publishing:

```python
                ticker = str(row.get("symbol") or "").upper()
                if ticker:
                    universe.append({"ticker": ticker, "name": str(row.get("name") or "")})
```

After the page loop, before the final `logger.info`:

```python
        # The content collectors resolve symbols against this universe. A write
        # failure must not cost us the price/volume events we already published.
        if universe:
            try:
                await self._cache.set_json(LEXICON_KEY, universe, ttl_seconds=86400)
            except Exception:
                logger.warning("lexicon publish failed", exc_info=True)
```

- [ ] **Step 4: Wire it in the service entrypoint**

In `services/collector-coingecko/app/main.py`, change line 28 from:

```python
    collector = CoinGeckoCollector(client, producer)
```

to:

```python
    collector = CoinGeckoCollector(client, producer, cache=cache)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_coingecko_lexicon_publish.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add services/collector-coingecko/ tests/test_coingecko_lexicon_publish.py
git commit -m "feat(coingecko): publish the token universe to Redis for symbol resolution"
```

---

### Task 7: Wire both content collectors, and drop the worker's MARKET fallback

**Files:**
- Modify: `services/collector-social/app/main.py:79-98`
- Modify: `services/collector-news/app/main.py:42-67`
- Modify: `services/sentiment-service/app/worker.py:67`
- Test: `tests/test_sentiment_worker_symbols.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sentiment_worker_symbols.py`:

```python
"""The worker no longer invents symbols: normalization guarantees them upstream."""

from __future__ import annotations

from datetime import UTC, datetime

from cmi_common.sources import RawItem
from service_modules import load_service_module

SentimentDbWorker = load_service_module(
    "sentiment-service", "worker"
).SentimentDbWorker


class Row:
    """Stands in for a raw_content row as fetch_unscored returns it."""

    def __init__(self, symbols: list[str]) -> None:
        self.id = 1
        self.kind = "news"
        self.source = "rss"
        self.title = "SEC approves a new framework"
        self.text = ""
        self.symbols = symbols
        self.engagement = 0.0
        self.published_at = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


class FakeRepo:
    def __init__(self, rows: list[Row]) -> None:
        self._rows = rows
        self.aggregated: list[str] = []

    async def fetch_unscored(self, batch: int) -> list[Row]:
        rows, self._rows = self._rows, []
        return rows

    async def mark_scored(self, row_id: int, **kw: object) -> None:
        return None

    async def upsert_aggregate(self, *, symbol: str, **kw: object) -> None:
        self.aggregated.append(symbol)


class FakeScorer:
    class _R:
        score = 0.5
        confidence = 0.9
        model_name = "fake"

    def score(self, text: str) -> _R:
        return self._R()


class FakeProducer:
    async def publish(self, topic: object, event: object) -> None:
        return None


async def test_market_is_taken_from_the_row_not_invented() -> None:
    # Gate 3 assigns MARKET at collection time. A fallback here would silently
    # re-admit rows the relevance gate was supposed to have rejected.
    repo = FakeRepo([Row(symbols=["MARKET"])])
    worker = SentimentDbWorker(repo, FakeScorer(), FakeProducer())
    await worker.run_once()
    assert repo.aggregated == ["MARKET"]


async def test_a_symbolless_row_aggregates_nothing() -> None:
    # If such a row ever reaches the worker the invariant is broken upstream;
    # the worker must not paper over it by inventing MARKET.
    repo = FakeRepo([Row(symbols=[])])
    worker = SentimentDbWorker(repo, FakeScorer(), FakeProducer())
    await worker.run_once()
    assert repo.aggregated == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sentiment_worker_symbols.py -v`
Expected: `test_a_symbolless_row_aggregates_nothing` FAILS with `assert ['MARKET'] == []` — the worker's `or ["MARKET"]` fallback invents the symbol. The first test passes already.

- [ ] **Step 3: Remove the worker fallback**

In `services/sentiment-service/app/worker.py`, replace line 67:

```python
            symbols = row.symbols or ["MARKET"]
```

with:

```python
            # Every stored row carries at least one symbol: the collectors'
            # normalizer assigns MARKET to symbol-less crypto content and drops
            # everything else. A fallback here would mask a broken invariant.
            symbols = row.symbols
```

- [ ] **Step 4: Wire collector-social**

In `services/collector-social/app/main.py`, add to the imports:

```python
from cmi_common.sources import LexiconLoader, LexiconNormalizer
```

In `_startup`, after `providers = _build_providers()`:

```python
    normalizer = LexiconNormalizer(
        LexiconLoader(cache), service="collector-social"
    )
```

and pass `normalizer=normalizer` to the `AdaptivePollLoop(...)` construction inside the list comprehension.

- [ ] **Step 5: Wire collector-news**

In `services/collector-news/app/main.py`, add the same import. In `_startup`, after the `providers` list is built:

```python
    normalizer = LexiconNormalizer(LexiconLoader(cache), service="collector-news")
```

and pass `normalizer=normalizer` to the `AdaptivePollLoop(...)` construction.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: all tests pass, including the pre-existing suite.

- [ ] **Step 7: Lint and commit**

```bash
make lint
git add services/collector-social/app/main.py services/collector-news/app/main.py \
        services/sentiment-service/app/worker.py tests/test_sentiment_worker_symbols.py
git commit -m "feat(collectors): apply the normalizer; drop the worker MARKET fallback"
```

---

### Task 8: Golden-file test against real production rows

**Files:**
- Create: `tests/fixtures/production_rows.json`
- Create: `tests/test_normalizer_golden.py`

This is the task that proves the plan solved the actual problem rather than a hypothetical one. Every fixture below is a verbatim row observed on the production VPS on 2026-07-26.

- [ ] **Step 1: Write the fixture file**

Create `tests/fixtures/production_rows.json`:

```json
[
  {
    "case": "newsdata tags a Hyperliquid article with six unrelated coins",
    "source": "newsdata",
    "kind": "news",
    "title": "Hyperliquid Price Prediction Today: Key Breakout Levels for HYPE",
    "text": "",
    "provider_symbols": ["JST", "FLOW", "KEEP", "BTC", "ONE", "USDT", "NEAR"],
    "expect_kept": true,
    "expect_symbols": ["HYPE"]
  },
  {
    "case": "newsdata tags a Bitcoin article with coins absent from the text",
    "source": "newsdata",
    "kind": "news",
    "title": "Bitcoin (BTC) Slides Under $64K Amid Rising Treasury Yields and Weak Stablecoin Activity",
    "text": "",
    "provider_symbols": ["ETH", "BTC", "ONE", "USDT", "REKT", "BAND", "USDC"],
    "expect_kept": true,
    "expect_symbols": ["BTC"]
  },
  {
    "case": "gdelt returns a football article for the cryptocurrency query",
    "source": "gdelt",
    "kind": "news",
    "title": "Chelsea eye January move for defender as talks progress",
    "text": "",
    "provider_symbols": [],
    "expect_kept": false,
    "expect_symbols": []
  },
  {
    "case": "regulatory news naming no coin becomes the market regime signal",
    "source": "rss",
    "kind": "news",
    "title": "SEC approves a new regulatory framework for crypto exchanges",
    "text": "",
    "provider_symbols": [],
    "expect_kept": true,
    "expect_symbols": ["MARKET"]
  },
  {
    "case": "an explicit cashtag confirms an otherwise ambiguous ticker",
    "source": "bluesky",
    "kind": "social",
    "title": null,
    "text": "$ONE looking strong today",
    "provider_symbols": [],
    "expect_kept": true,
    "expect_symbols": ["ONE"]
  },
  {
    "case": "the bare English word must not become the Harmony token",
    "source": "bluesky",
    "kind": "social",
    "title": null,
    "text": "ONE more day until the halving",
    "provider_symbols": [],
    "expect_kept": true,
    "expect_symbols": ["MARKET"]
  }
]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_normalizer_golden.py`:

```python
"""Golden cases captured from production on 2026-07-26.

These are the rows that motivated the whole normalization layer. If one of them
regresses, the pipeline is mis-attributing sentiment again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmi_common.sources import ContentNormalizer, RawItem, SymbolLexicon
from cmi_common.sources.vocab import SEED_COINS

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "production_rows.json").read_text(
        encoding="utf-8"
    )
)

# The live top-N universe holds these too. They are the tickers NewsData
# hallucinated onto these very articles, so the fixtures only prove anything if
# the lexicon actually knows them — otherwise they would be rejected for being
# out of universe rather than for being uncorroborated.
_EXTRA = [
    {"ticker": "JST", "name": "JUST"},
    {"ticker": "KEEP", "name": "Keep Network"},
    {"ticker": "ONE", "name": "Harmony"},
    {"ticker": "BAND", "name": "Band Protocol"},
]
LEXICON = SymbolLexicon.from_coins(
    [{"ticker": t, "name": n} for t, n in SEED_COINS] + _EXTRA
)


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_production_row(case: dict) -> None:
    item = RawItem(
        source=case["source"],
        kind=case["kind"],
        external_id=case["case"],
        title=case["title"],
        text=case["text"],
        symbols=case["provider_symbols"],
    )
    result = ContentNormalizer(LEXICON).apply([item])
    if case["expect_kept"]:
        assert result.kept, f"{case['case']}: expected kept, was dropped"
        assert result.kept[0].symbols == case["expect_symbols"]
    else:
        assert not result.kept, f"{case['case']}: expected dropped, was kept"
```

- [ ] **Step 3: Run the golden tests**

Run: `pytest tests/test_normalizer_golden.py -v`
Expected: 6 passed

If the Hyperliquid case fails on `HYPE`, check that `HYPE`/`Hyperliquid` is in `SEED_COINS` (it is, in Task 1 Step 3) — the title contains both the uppercase token `HYPE` and the name "Hyperliquid", so it must resolve twice over.

- [ ] **Step 4: Run the whole suite and lint**

Run: `pytest -q && make lint`
Expected: everything passes.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/production_rows.json tests/test_normalizer_golden.py
git commit -m "test(sources): golden cases from the production rows that motivated the gate"
```

---

## Verification before calling phase 1 done

- [ ] `pytest -q` — full suite green
- [ ] `make lint` — ruff, black --check and mypy strict all clean
- [ ] `make up` then check `collector-social` logs: lines still read `<provider> ingested N new items`, and `curl localhost:8000/metrics | grep cmi_content_dropped_total` on a collector shows non-zero drops for `gdelt`
- [ ] Confirm in Redis that the universe is published: `docker exec bottrading-redis-1 redis-cli GET lexicon:coins | head -c 200`
- [ ] Spot-check the DB after ~30 min of running: `SELECT source, symbols, left(title,80) FROM raw_content ORDER BY fetched_at DESC LIMIT 20;` — no row should carry a symbol absent from its own title/text

Phase 2 (the data wipe) is only run **after** this checklist passes on the VPS. Wiping before the gate is live would just re-pollute the table.
