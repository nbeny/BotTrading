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
    r"\b("
    + "|".join(sorted(map(re.escape, CRYPTO_KEYWORDS), key=len, reverse=True))
    + r")\b"
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
            if symbol is None or (
                self._lex.is_ambiguous(symbol) and symbol not in named
            ):
                continue
            confirmed.add(symbol)

        return sorted(confirmed)
