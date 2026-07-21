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


def is_whitelisted(symbol: str) -> bool:
    return symbol.upper() in _WHITELIST


def to_kraken_pair(symbol: str) -> str:
    try:
        return _WHITELIST[symbol.upper()]
    except KeyError as exc:
        raise UnknownSymbol(symbol) from exc
