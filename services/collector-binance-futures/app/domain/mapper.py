"""Binance futures rows -> DerivativesEvent."""

from __future__ import annotations

from decimal import Decimal

from cmi_common.events import DerivativesEvent
from cmi_common.events.base import Source

from .symbols import resolve

#: Binance settles funding every 8 hours: three periods a day.
PERIODS_PER_YEAR = 3 * 365


def to_derivatives_events(
    rows: list[dict], *, priced: set[str], ambiguous: set[str]
) -> list[DerivativesEvent]:
    """One event per resolvable perp carrying a funding rate.

    A row without `lastFundingRate` produces nothing: an absent rate must reach
    the scorer as an absent axis, never as a neutral zero. A rate of exactly
    zero is a different matter — that is balanced positioning, measured, and it
    is carried through.
    """
    events: list[DerivativesEvent] = []
    for row in rows:
        symbol = resolve(
            str(row.get("symbol") or ""), priced=priced, ambiguous=ambiguous
        )
        if symbol is None:
            continue
        raw_rate = row.get("lastFundingRate")
        if raw_rate is None or raw_rate == "":
            continue
        rate = float(raw_rate)
        events.append(
            DerivativesEvent(
                source=Source.BINANCE_FUTURES,
                symbol=symbol,
                funding_rate_8h=rate,
                funding_annualized_pct=round(rate * PERIODS_PER_YEAR * 100, 4),
            )
        )
    return events


def with_majors_detail(
    event: DerivativesEvent,
    *,
    open_interest_usd: Decimal | None,
    oi_change_pct_24h: float | None,
    long_short_ratio: float | None,
) -> DerivativesEvent:
    """The same event with the per-symbol majors readings folded in.

    Events are frozen, so this returns a copy rather than mutating.
    """
    return event.model_copy(
        update={
            "open_interest_usd": open_interest_usd,
            "open_interest_change_pct_24h": oi_change_pct_24h,
            "long_short_account_ratio": long_short_ratio,
        }
    )
