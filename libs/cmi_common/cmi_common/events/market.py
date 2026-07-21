"""Market data events: price, volume and DEX activity."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType


class PriceEvent(BaseEvent):
    """Emitted by the CoinGecko collector on ``market.price.events``."""

    event_type: Literal[EventType.PRICE] = EventType.PRICE
    symbol: str = Field(..., description="Ticker, upper-case, e.g. 'SOL'")
    coin_id: str = Field(..., description="Provider coin id, e.g. 'solana'")
    price_usd: Decimal = Field(..., gt=0)
    market_cap_usd: Decimal | None = Field(default=None, ge=0)
    volume_24h_usd: Decimal | None = Field(default=None, ge=0)
    price_change_pct_1h: float | None = None
    price_change_pct_24h: float | None = None
    price_change_pct_7d: float | None = None
    market_cap_rank: int | None = Field(default=None, ge=1)
    is_trending: bool = False

    def partition_key(self) -> str:
        return self.symbol


class VolumeEvent(BaseEvent):
    """Significant volume move, emitted on ``market.volume.events``."""

    event_type: Literal[EventType.VOLUME] = EventType.VOLUME
    symbol: str
    coin_id: str | None = None
    volume_24h_usd: Decimal = Field(..., ge=0)
    # Ratio vs the trailing average; > 1 means an abnormal surge.
    volume_spike_ratio: float = Field(..., ge=0)
    window_minutes: int = Field(default=60, ge=1)

    def partition_key(self) -> str:
        return self.symbol


class DexEvent(BaseEvent):
    """New pool / token / liquidity movement from DexScreener.

    Published on ``market.dex.events``.
    """

    event_type: Literal[EventType.DEX] = EventType.DEX
    symbol: str
    chain: str = Field(..., description="Chain id, e.g. 'solana', 'ethereum'")
    dex_id: str = Field(..., description="DEX name, e.g. 'raydium', 'uniswap'")
    pair_address: str
    base_token_address: str
    quote_token_symbol: str = "USDC"
    price_usd: Decimal | None = Field(default=None, ge=0)
    liquidity_usd: Decimal | None = Field(default=None, ge=0)
    volume_24h_usd: Decimal | None = Field(default=None, ge=0)
    price_change_pct_5m: float | None = None
    price_change_pct_1h: float | None = None
    pair_created_at: int | None = Field(default=None, description="epoch ms")
    is_new_pool: bool = False
    # Cheap heuristics computed by the collector before deep analysis.
    txns_buys_5m: int | None = Field(default=None, ge=0)
    txns_sells_5m: int | None = Field(default=None, ge=0)

    def partition_key(self) -> str:
        return self.pair_address
