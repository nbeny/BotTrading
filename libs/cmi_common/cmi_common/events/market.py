"""Market data events: price, volume, DEX activity, derivatives positioning
and protocol fundamentals."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType


class PriceEvent(BaseEvent):
    """Emitted by the CoinGecko collector on ``market.price.events``."""

    event_type: Literal[EventType.PRICE] = EventType.PRICE
    symbol: str = Field(..., description="Ticker, upper-case, e.g. 'SOL'")
    coin_id: str = Field(..., description="Provider coin id, e.g. 'solana'")
    name: str | None = Field(default=None, description="Display name, e.g. 'Solana'")
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


class DerivativesEvent(BaseEvent):
    """Perp positioning from Binance futures, on ``market.derivatives.events``.

    Every field is nullable on purpose: the broad tier supplies funding for
    every perp in one request, while open interest and the long/short ratio are
    per-symbol calls made only for majors. A partial event is the normal case,
    not a degraded one.
    """

    event_type: Literal[EventType.DERIVATIVES] = EventType.DERIVATIVES
    symbol: str
    #: Raw fraction as Binance returns it: 0.0001 == 0.01% per 8h period.
    funding_rate_8h: float | None = None
    #: Derived from funding_rate_8h (producer-side arithmetic, not an
    #: independent measurement) purely so a human reads "10.95%" instead of
    #: "0.0001". The two can disagree if only one is ever updated.
    funding_annualized_pct: float | None = None
    open_interest_usd: Decimal | None = Field(default=None, ge=0)
    open_interest_change_pct_24h: float | None = None
    #: gt=0, not ge=0: the scorer computes _sigmoid(-log(ratio)), so a zero
    #: would be log(0). Rejected at construction, not in the scorer.
    long_short_account_ratio: float | None = Field(default=None, gt=0)

    def partition_key(self) -> str:
        return self.symbol


class FundamentalsEvent(BaseEvent):
    """Protocol fundamentals and scheduled dilution, on
    ``market.fundamentals.events``.

    ``has_unlock_schedule`` is what keeps "no unlock is coming" distinct from
    "DefiLlama does not track this token". Only a minority of tokens have a
    published schedule, so collapsing the two would turn silence into a
    reassurance the data does not support.
    """

    event_type: Literal[EventType.FUNDAMENTALS] = EventType.FUNDAMENTALS
    symbol: str
    coin_id: str = Field(
        ...,
        description=(
            "CoinGecko id, e.g. 'aave' -- not a DefiLlama id. This is "
            "DefiLlama's 'gecko_id', kept in our CoinGecko namespace so it "
            "joins directly to Token.coin_id; a wrong-namespace join here "
            "fails by returning nothing, not by erroring."
        ),
    )
    tvl_usd: Decimal | None = Field(default=None, ge=0)
    tvl_change_pct_7d: float | None = None
    #: Deliberately unbounded below, unlike ``tvl_usd``. Fees are a net *flow*
    #: and can genuinely be negative — a protocol can pay out more than it
    #: takes in. Measured on the live API: 8 of 2,514 fee rows carry a negative
    #: total24h (azuro at -$14,431, fusion-by-ipor at -$15,325). A ``ge=0``
    #: here raised ValidationError inside the collector's emit loop, which has
    #: no per-event guard, so one such protocol entering our universe would
    #: publish nothing for *any* token that cycle. TVL keeps its bound because
    #: it is a stock: negative TVL is nonsense data, not a measurement.
    fees_24h_usd: Decimal | None = None
    fees_change_pct_7d: float | None = None
    #: None with ``has_unlock_schedule`` True means: schedule known, nothing
    #: within the next 30 days.
    next_unlock_at: datetime | None = None
    next_unlock_pct_supply: float | None = Field(default=None, ge=0)
    has_unlock_schedule: bool = False

    def partition_key(self) -> str:
        return self.symbol
