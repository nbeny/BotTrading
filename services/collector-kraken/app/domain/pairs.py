"""Pure Kraken pair reference — AssetPairs payload in, tradable specs out.

Kraken is the venue this bot actually executes on, so its pair list is also the
tradability filter: a symbol with no Kraken pair has no candles, no book, and no
business producing a trade signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Kraken spells two assets the way nobody else does.
KRAKEN_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}

QUOTE = "USD"


@dataclass(frozen=True, slots=True)
class VenuePairSpec:
    symbol: str  # normalized ticker, e.g. "BTC"
    pair: str  # Kraken pair id used in API calls, e.g. "XXBTZUSD"
    ordermin: Decimal | None


def normalize_base(base: str) -> str:
    up = base.upper()
    return KRAKEN_ALIASES.get(up, up)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse_asset_pairs(payload: dict[str, Any]) -> list[VenuePairSpec]:
    """Online, USD-quoted pairs from a ``/0/public/AssetPairs`` response."""
    out: list[VenuePairSpec] = []
    for pair_id, entry in (payload.get("result") or {}).items():
        wsname = entry.get("wsname")
        if not wsname or "/" not in wsname:
            continue
        base, _, quote = wsname.partition("/")
        if quote.upper() != QUOTE:
            continue
        if entry.get("status", "online") != "online":
            continue
        out.append(
            VenuePairSpec(
                symbol=normalize_base(base),
                pair=pair_id,
                ordermin=_decimal(entry.get("ordermin")),
            )
        )
    return out
