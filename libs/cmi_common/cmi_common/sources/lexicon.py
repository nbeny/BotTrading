"""The token universe: what counts as a symbol, and which tickers lie.

``SymbolLexicon`` is an immutable snapshot built from CoinGecko's top-N coins.
It answers three questions and nothing else: does this token resolve to a coin,
which coin names appear in this text, and is this ticker one of the homographs
(ONE, KEEP, FLOW...) that must be corroborated before it is believed.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..observability import LEXICON_COINS
from .vocab import COMMON_WORDS, SEED_COINS

# A coin name shorter than this matches far too much ordinary prose to index.
_MIN_NAME_LEN = 4


def _is_prose(name: str) -> bool:
    """True for a coin name an ordinary sentence can produce by accident.

    The homograph guard protects the *ticker* channel: bare ``FLOW`` is
    disbelieved unless corroborated. Coin *names* had no such guard, so "cash
    flow analysis" resolved to FLOW and "the market maker" to MKR — and worse, a
    name match is itself what corroborates an ambiguous ticker, so one stray
    noun unlocked both channels at once.

    Word count is not the test. An earlier version exempted multi-word names on
    the theory that prose does not produce them by accident, and "The Graph"
    (GRT, in the seed) disproved it immediately: "as the graph shows, inflation
    cooled" booked GRT. A name is prose when *every* word in it is ordinary
    English, so "The Graph" and "The Sandbox" qualify while "Bitcoin Cash" and
    "Keep Network" do not.
    """
    return all(word.upper() in COMMON_WORDS for word in name.split())


def _alternation(names: Iterable[str]) -> re.Pattern[str] | None:
    """One word-bounded alternation, longest first so "bitcoin cash" beats "bitcoin"."""
    ordered = sorted(names, key=len, reverse=True)
    if not ordered:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in ordered) + r")\b")


@dataclass(frozen=True, slots=True)
class SymbolLexicon:
    by_ticker: Mapping[str, str]
    by_name: Mapping[str, str]
    #: Single-word names that are also ordinary English ("Flow", "Maker"). Kept
    #: apart because seeing one proves nothing on its own — see prose_names_in.
    prose_names: Mapping[str, str]
    ambiguous: frozenset[str]
    _name_re: re.Pattern[str] | None
    _prose_re: re.Pattern[str] | None

    @classmethod
    def from_coins(cls, coins: Iterable[Mapping[str, str]]) -> SymbolLexicon:
        """Build a snapshot from ``[{"ticker": "BTC", "name": "Bitcoin"}, ...]``."""
        by_ticker: dict[str, str] = {}
        by_name: dict[str, str] = {}
        prose_names: dict[str, str] = {}
        for coin in coins:
            ticker = (coin.get("ticker") or "").strip().upper()
            name = (coin.get("name") or "").strip().lower()
            if not ticker:
                continue
            by_ticker[ticker] = ticker
            if len(name) >= _MIN_NAME_LEN:
                target = prose_names if _is_prose(name) else by_name
                target[name] = ticker
        ambiguous = frozenset(t for t in by_ticker if t in COMMON_WORDS)
        return cls(
            by_ticker,
            by_name,
            prose_names,
            ambiguous,
            _alternation(by_name),
            _alternation(prose_names),
        )

    def resolve_ticker(self, token: str) -> str | None:
        """Canonical symbol for a ticker-shaped token, or None if out of universe."""
        return self.by_ticker.get(token.strip().upper())

    def names_in(self, lowered_text: str) -> set[str]:
        """Symbols whose full coin name appears in `lowered_text`, and is trusted.

        Excludes prose names — "cash flow analysis" must not mean the FLOW token.
        """
        if self._name_re is None:
            return set()
        return {self.by_name[m] for m in self._name_re.findall(lowered_text)}

    def prose_names_in(self, lowered_text: str) -> set[str]:
        """Symbols whose name appeared but reads as ordinary English.

        Not evidence by itself. It corroborates the coin's own ticker, so
        "Dash (DASH) surges" resolves while "a dash of salt" does not.
        """
        if self._prose_re is None:
            return set()
        return {self.prose_names[m] for m in self._prose_re.findall(lowered_text)}

    def is_ambiguous(self, symbol: str) -> bool:
        """True when the ticker is also an ordinary English word."""
        return symbol in self.ambiguous


SEED_LEXICON = SymbolLexicon.from_coins(
    [{"ticker": t, "name": n} for t, n in SEED_COINS]
)

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

    The seed is a stopgap, not a resting state: while it is what we are serving,
    Redis is re-probed on ``seed_retry_seconds`` instead of the full refresh
    window. On a cold stack the content collectors start alongside
    collector-coingecko, so ``lexicon:coins`` is reliably absent for the first
    few seconds — without the short retry they would spend a quarter of an hour
    resolving against 50 coins instead of the top 200, silently booking real
    symbols as MARKET.
    """

    def __init__(
        self,
        cache: _CacheLike,
        *,
        service: str = "unknown",
        refresh_seconds: float = 900.0,
        seed_retry_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache = cache
        self._service = service
        self._refresh = refresh_seconds
        self._seed_retry = seed_retry_seconds
        self._clock = clock
        self._lexicon: SymbolLexicon | None = None
        self._loaded_at = 0.0

    async def get(self) -> SymbolLexicon:
        now = self._clock()
        window = self._seed_retry if self._lexicon is SEED_LEXICON else self._refresh
        if self._lexicon is not None and now - self._loaded_at < window:
            return self._lexicon
        try:
            coins = await self._cache.get_json(LEXICON_KEY)
        except Exception:
            logger.warning("lexicon read failed; keeping previous", exc_info=True)
            coins = None
        if coins:
            if self._lexicon is SEED_LEXICON:
                logger.info("lexicon loaded from redis; leaving the bundled seed")
            self._lexicon = SymbolLexicon.from_coins(coins)
        elif self._lexicon is None:
            self._lexicon = SEED_LEXICON
        if self._lexicon is SEED_LEXICON:
            # Silent degradation is the failure mode that hurts: resolving
            # against 50 coins instead of the live 200 books genuine symbols as
            # MARKET, and nothing downstream can tell. Paced by the retry window.
            logger.warning(
                "%s missing; resolving against the %d-coin bundled seed",
                LEXICON_KEY,
                len(self._lexicon.by_ticker),
            )
        LEXICON_COINS.labels(self._service).set(len(self._lexicon.by_ticker))
        # Stamped on every probe, successful or not, so a persistently empty
        # Redis is re-read on the window rather than on every single call.
        self._loaded_at = now
        return self._lexicon
