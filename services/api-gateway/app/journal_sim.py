"""Walk a price path and report how a position would have ended.

Comparing entry price to the price at the horizon systematically overstates
performance: a position stopped out at -5% whose price later recovers would read
as a winner. This walks the path in order and applies whichever bound is touched
first.

Known limitations, all biasing the result optimistic: no slippage, no book
depth, ~60s price sampling (a sub-minute wick through the stop is invisible),
no execution latency. A marginally profitable result here should be treated as
unprofitable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Taker fee per side, matching read_api.map_portfolio_trade.
DEFAULT_FEE_PCT = 0.0016


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    outcome: str                  # stop_loss | take_profit | horizon | no_data
    exit_price: float | None
    seconds_held: int | None
    pnl_gross_pct: float | None
    pnl_net_pct: float | None


_NO_DATA = TradeOutcome("no_data", None, None, None, None)


def simulate_path(
    *,
    entry: float,
    direction: str,
    stop_loss: float,
    take_profit: float,
    path: Sequence[tuple[int, float]],
    fee_pct: float = DEFAULT_FEE_PCT,
) -> TradeOutcome:
    """``path`` is (seconds_since_entry, price), chronological.

    An empty path returns ``no_data`` rather than a zero P&L: a collector outage
    must read as an absence, not as a neutral position.
    """
    if entry <= 0 or not path:
        return _NO_DATA

    is_long = direction != "short"

    for seconds, price in path:
        if is_long:
            hit_stop, hit_target = price <= stop_loss, price >= take_profit
        else:
            hit_stop, hit_target = price >= stop_loss, price <= take_profit
        # Stop checked first: within one sampling interval we cannot know which
        # came first, and assuming the favourable one is how a backtest lies.
        if hit_stop:
            return _settle("stop_loss", entry, price, seconds, is_long, fee_pct)
        if hit_target:
            return _settle("take_profit", entry, price, seconds, is_long, fee_pct)

    seconds, price = path[-1]
    return _settle("horizon", entry, price, seconds, is_long, fee_pct)


def _settle(
    outcome: str, entry: float, exit_price: float, seconds: int,
    is_long: bool, fee_pct: float,
) -> TradeOutcome:
    move = (exit_price - entry) / entry
    gross = (move if is_long else -move) * 100
    # Fees apply on entry and exit regardless of direction or outcome.
    net = gross - fee_pct * 200
    return TradeOutcome(outcome, exit_price, seconds, round(gross, 6), round(net, 6))
