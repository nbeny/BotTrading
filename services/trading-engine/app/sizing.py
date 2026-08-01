"""Pure position sizing: turn a portfolio fraction into a contract quantity.

Conservative by construction: notional is capped by both MAX_ORDER_USD and
equity * MAX_LEVERAGE, then floored to the exchange contract step. A size below
the exchange minimum returns 0.0 (caller rejects the signal).
"""

from __future__ import annotations

import math


def compute_size(
    *,
    equity_usd: float,
    position_size_pct: float,
    entry_price: float,
    max_order_usd: float,
    max_leverage: float,
    contract_step: float,
    min_contracts: float,
) -> float:
    if entry_price <= 0 or equity_usd <= 0 or position_size_pct <= 0:
        return 0.0
    notional = equity_usd * position_size_pct
    notional = min(notional, max_order_usd, equity_usd * max_leverage)
    raw = notional / entry_price
    steps = math.floor(raw / contract_step)
    size = round(steps * contract_step, 8)
    if size < min_contracts:
        return 0.0
    return size
