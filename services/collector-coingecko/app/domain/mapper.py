"""Pure domain mapping: CoinGecko payload -> typed events. No I/O here."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cmi_common.events.base import Source
from cmi_common.events.market import PriceEvent, VolumeEvent

# A volume surge is flagged when 24h volume exceeds this multiple of market cap
# turnover heuristic; tuned conservatively for the collector's cheap pre-filter.
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
    """Emit a VolumeEvent only when volume/market-cap turnover looks abnormal."""
    volume = _dec(row.get("total_volume"))
    mcap = _dec(row.get("market_cap"))
    if not volume or not mcap or mcap == 0:
        return None
    ratio = float(volume / mcap) * 10  # scale turnover into a spike-like ratio
    if ratio < VOLUME_SPIKE_MIN_RATIO:
        return None
    return VolumeEvent(
        source=Source.COINGECKO,
        symbol=str(row["symbol"]).upper(),
        coin_id=row["id"],
        volume_24h_usd=volume,
        volume_spike_ratio=round(ratio, 2),
        window_minutes=1440,
    )
