"""Strict symbol whitelist. A symbol not in this map is NEVER traded.

Maps the platform's internal symbol (e.g. "SOL") to a Kraken Futures perpetual
product id (e.g. "PF_SOLUSD"). Extend deliberately; unknown symbols are rejected.
"""

from __future__ import annotations


class UnknownSymbol(ValueError):
    """Raised when a symbol is not in the whitelist."""


# Internal symbol -> Kraken Futures perpetual product id.
_WHITELIST: dict[str, str] = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "DOGE": "PF_DOGEUSD",
    "AVAX": "PF_AVAXUSD",
    "LINK": "PF_LINKUSD",
    "MATIC": "PF_MATICUSD",
}


def normalize(symbol: str) -> str:
    """Reduce a user-facing symbol to its base ticker.

    The UI may send pair notation like ``"BTC/USDT"`` or ``"BTC-PERP"``; the
    whitelist is keyed by the bare base ticker (``"BTC"``). Split on the quote
    separator and take the base. Bare tickers (auto-signal path) pass through.
    """
    base = symbol.upper()
    for sep in ("/", "-", ":"):
        if sep in base:
            return base.split(sep)[0]
    return base


def is_whitelisted(symbol: str) -> bool:
    return normalize(symbol) in _WHITELIST


def to_kraken_pair(symbol: str) -> str:
    try:
        return _WHITELIST[normalize(symbol)]
    except KeyError as exc:
        raise UnknownSymbol(symbol) from exc
