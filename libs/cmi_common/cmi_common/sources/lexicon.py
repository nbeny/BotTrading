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
