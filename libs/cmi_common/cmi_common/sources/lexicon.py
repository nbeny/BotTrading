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
