"""Resolve a Binance perp ticker to a symbol this platform prices.

Only USDT-margined perps are considered. Coin-margined contracts (`BTCUSD_PERP`)
quote in the asset itself and carry their own funding curve; folding them into
the same reading would average two different things.
"""

from __future__ import annotations

QUOTE = "USDT"


def base_symbol(ticker: str) -> str | None:
    """`BTCUSDT` -> `BTC`, or None for any other quote."""
    if "_" in ticker or not ticker.endswith(QUOTE):
        return None
    base = ticker[: -len(QUOTE)]
    return base or None


def resolve(ticker: str, *, priced: set[str], ambiguous: set[str]) -> str | None:
    """The symbol this perp belongs to, if we price it unambiguously."""
    base = base_symbol(ticker)
    if base is None or base not in priced or base in ambiguous:
        return None
    return base
