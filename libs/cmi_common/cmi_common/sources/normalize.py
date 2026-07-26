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

from ..observability import CONTENT_DROPPED
from .lexicon import LexiconLoader, SymbolLexicon
from .raw import RawItem
from .vocab import STRONG_CRYPTO_KEYWORDS, WEAK_CRYPTO_KEYWORDS

#: Symbol carrying crypto-relevant content that names no specific coin.
MARKET_SYMBOL = "MARKET"

DROP_NOT_RELEVANT = "not_relevant"
DROP_EMPTY_TEXT = "empty_text"

# Uppercase body only. A lowercase one is a spelled-out amount, not a ticker:
# "$one million" and "$trillion" otherwise resolved to ONE and to an invented
# TRILLION -- and because a cashtag also satisfies the relevance gate, the item
# skipped every other check on its way into the database.
_CASHTAG = re.compile(r"\$([A-Z][A-Z0-9]{1,9})\b")
_UPPER_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\b")
# Social copy is routinely all-lowercase ("btc is ripping right now"), which
# matched neither the ticker nor the name channel and was dropped outright --
# real content loss on exactly the sources this pipeline leans on. Three chars
# minimum: two-letter lowercase words are noise.
_LOWER_TOKEN = re.compile(r"\b([a-z][a-z0-9]{2,9})\b")


def _keyword_re(words: frozenset[str]) -> re.Pattern[str]:
    """Word-bounded alternation, tolerating a plural "s" ("exchanges", "wallets")."""
    return re.compile(
        r"\b("
        + "|".join(sorted(map(re.escape, words), key=len, reverse=True))
        + r")s?\b"
    )


_STRONG_KEYWORD = _keyword_re(STRONG_CRYPTO_KEYWORDS)
_WEAK_KEYWORD = _keyword_re(WEAK_CRYPTO_KEYWORDS)


def _is_crypto_relevant(lowered: str) -> bool:
    """One specific term, or two distinct generic ones."""
    if _STRONG_KEYWORD.search(lowered):
        return True
    return len(set(_WEAK_KEYWORD.findall(lowered))) >= 2


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
            relevant = _is_crypto_relevant(text.lower())
            known, unknown = self._resolve(text)
            # An out-of-universe cashtag is how a brand-new token gets in, but on
            # its own it is just a $-prefixed word: "$TSLA and $NVDA are leading
            # the rally" would otherwise book two equities as crypto symbols.
            # Something else in the item has to say crypto first.
            if unknown and (known or relevant):
                known |= unknown
            symbols = sorted(known)
            if not symbols:
                if not relevant:
                    dropped.append((item, DROP_NOT_RELEVANT))
                    continue
                symbols = [MARKET_SYMBOL]
            kept.append(item.model_copy(update={"symbols": symbols}))
        return NormalizeResult(kept=kept, dropped=dropped)

    def _resolve(self, text: str) -> tuple[set[str], set[str]]:
        """(symbols confirmed in the universe, cashtagged tokens outside it)."""
        lowered = text.lower()
        named = self._lex.names_in(lowered)
        # A name that reads as ordinary English ("Flow", "Maker") is not evidence
        # on its own, but it does corroborate its own ticker.
        prose = self._lex.prose_names_in(lowered)
        confirmed: set[str] = set(named)

        # A cashtag is an explicit claim by the author, so it confirms an
        # ambiguous ticker and is the only way an out-of-universe token gets in.
        unknown: set[str] = set()
        for token in _CASHTAG.findall(text):
            symbol = self._lex.resolve_ticker(token)
            if symbol is None:
                unknown.add(token)
            else:
                confirmed.add(symbol)

        # A bare uppercase token is believed only if it is in the universe and
        # is not an ordinary English word that happens to be a ticker.
        for token in _UPPER_TOKEN.findall(text):
            symbol = self._lex.resolve_ticker(token)
            if symbol is None or (
                self._lex.is_ambiguous(symbol)
                and symbol not in named
                and symbol not in prose
            ):
                continue
            confirmed.add(symbol)

        # Lowercase is trusted only where the ticker is not an English word, so
        # "btc is ripping" resolves while "one more day" and "a ton of" cannot.
        for token in _LOWER_TOKEN.findall(lowered):
            symbol = self._lex.resolve_ticker(token)
            if symbol is not None and not self._lex.is_ambiguous(symbol):
                confirmed.add(symbol)

        return confirmed, unknown


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
