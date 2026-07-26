"""Pure domain mapping: CoinGecko payload -> typed events. No I/O here."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cmi_common.events.base import Source
from cmi_common.events.market import PriceEvent, VolumeEvent

# Kept for reference: the turnover multiple that used to gate emission. It no
# longer filters anything -- see to_volume_event. Downstream consumers that want
# a "spike" threshold should apply their own, where the scoring lives.
VOLUME_SPIKE_MIN_RATIO = 3.0


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def to_price_event(row: dict[str, Any], trending_ids: set[str]) -> PriceEvent:
    return PriceEvent(
        source=Source.COINGECKO,
        symbol=str(row["symbol"]).upper(),
        coin_id=row["id"],
        price_usd=_dec(row["current_price"]) or Decimal("0"),
        market_cap_usd=_dec(row.get("market_cap")),
        volume_24h_usd=_dec(row.get("total_volume")),
        price_change_pct_1h=row.get("price_change_percentage_1h_in_currency"),
        price_change_pct_24h=row.get("price_change_percentage_24h_in_currency"),
        price_change_pct_7d=row.get("price_change_percentage_7d_in_currency"),
        market_cap_rank=row.get("market_cap_rank"),
        is_trending=row["id"] in trending_ids,
    )


def to_volume_event(row: dict[str, Any]) -> VolumeEvent | None:
    """Emit a VolumeEvent whenever turnover can be computed at all.

    It used to fire only above ``VOLUME_SPIKE_MIN_RATIO``, i.e. a 24h volume
    worth 30% of market cap. No large-cap ever reaches that, so the volume
    factor was not weak for majors — it was structurally absent, and the scorer
    could not tell the two apart. Measured in production: 12174 analyses, and
    only two symbols ever escalated.

    Whether a turnover of 0.2 is interesting is the scorer's call, not the
    collector's. None is still returned when there is nothing to measure: an
    invented ratio would be counted as a supplied factor.
    """
    volume = _dec(row.get("total_volume"))
    mcap = _dec(row.get("market_cap"))
    if not volume or not mcap or mcap == 0:
        return None
    ratio = float(volume / mcap) * 10  # scale turnover into a spike-like ratio
    return VolumeEvent(
        source=Source.COINGECKO,
        symbol=str(row["symbol"]).upper(),
        coin_id=row["id"],
        volume_24h_usd=volume,
        volume_spike_ratio=round(ratio, 2),
        window_minutes=1440,
    )
