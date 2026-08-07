"""Pure mapping: Kraken public payloads -> typed candles and depth snapshots.

Every number Kraken returns is a string; all of them are parsed to Decimal and
never to float, matching the Numeric(38,12) columns they land in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from cmi_common.events.base import Source
from cmi_common.events.market import CandleEvent

#: `result` carries the series under the pair id plus this scalar cursor.
_CURSOR_KEY = "last"


@dataclass(frozen=True, slots=True)
class OhlcCandle:
    time: datetime
    symbol: str
    interval: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vwap: Decimal
    volume: Decimal
    trades: int


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    symbol: str
    mid_price: Decimal
    spread_pct: float
    bid_depth_usd: Decimal
    ask_depth_usd: Decimal


def _series(payload: dict[str, Any]) -> Any:
    """The single non-cursor value in `result`, or None."""
    result = payload.get("result") or {}
    for key, value in result.items():
        if key != _CURSOR_KEY:
            return value
    return None


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc


def parse_ohlc(
    payload: dict[str, Any], *, symbol: str, interval: str
) -> list[OhlcCandle]:
    rows = _series(payload)
    if not rows:
        return []
    out: list[OhlcCandle] = []
    for row in rows:
        try:
            out.append(
                OhlcCandle(
                    time=datetime.fromtimestamp(int(row[0]), tz=UTC),
                    symbol=symbol,
                    interval=interval,
                    open=_dec(row[1]),
                    high=_dec(row[2]),
                    low=_dec(row[3]),
                    close=_dec(row[4]),
                    vwap=_dec(row[5]),
                    volume=_dec(row[6]),
                    trades=int(row[7]),
                )
            )
        except (IndexError, TypeError, ValueError):
            # A malformed row is dropped, never allowed to kill the sweep: one
            # bad candle must not cost the other 719.
            continue
    return out


def candle_event(c: OhlcCandle) -> CandleEvent:
    """One OhlcCandle -> the event published on ``market.candle.events``.

    ``occurred_at`` carries the candle's start, already tz-aware from
    ``parse_ohlc``. Kraken returns ``vwap=0`` for a window with no trades
    (open/high/low/close then still carry the previous close) -- a price
    cannot legitimately be zero, so that reading is unmeasured and maps to
    ``None`` rather than leaking in as a confident zero-price tick.
    """
    return CandleEvent(
        source=Source.KRAKEN,
        occurred_at=c.time,
        symbol=c.symbol,
        interval=c.interval,
        open=c.open,
        high=c.high,
        low=c.low,
        close=c.close,
        # `> 0`, pas une verite : un vwap negatif serait une donnee aberrante a
        # laisser remonter, pas un « non mesure » a taire. La forme `or None`
        # ressemble en outre au `or 0` que la revue null-vs-zero traque.
        vwap=c.vwap if c.vwap > 0 else None,
        volume=c.volume,
        trades=c.trades,
    )


def parse_depth(
    payload: dict[str, Any], *, symbol: str, band_pct: float = 1.0
) -> DepthSnapshot | None:
    book = _series(payload)
    if not isinstance(book, dict):
        return None
    asks = book.get("asks") or []
    bids = book.get("bids") or []
    if not asks or not bids:
        return None
    try:
        best_ask = _dec(asks[0][0])
        best_bid = _dec(bids[0][0])
    except (IndexError, ValueError):
        return None
    mid = (best_ask + best_bid) / 2
    if mid <= 0:
        return None
    band = Decimal(str(band_pct)) / Decimal("100")
    floor, ceiling = mid * (1 - band), mid * (1 + band)

    def _notional(levels: list, keep) -> Decimal:
        total = Decimal("0")
        for level in levels:
            try:
                price, qty = _dec(level[0]), _dec(level[1])
            except (IndexError, ValueError):
                continue
            if keep(price):
                total += price * qty
        return total

    return DepthSnapshot(
        symbol=symbol,
        mid_price=mid,
        spread_pct=float((best_ask - best_bid) / mid * 100),
        bid_depth_usd=_notional(bids, lambda p: p >= floor),
        ask_depth_usd=_notional(asks, lambda p: p <= ceiling),
    )
