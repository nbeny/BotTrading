"""Market data events: price, volume, DEX activity, derivatives positioning,
protocol fundamentals and developer activity."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

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


class DeveloperEvent(BaseEvent):
    """Activité de développement agrégée par token, sur ``market.developer.events``.

    Tous les champs de mesure transportent des **ratios bruts**, pas des valeurs
    mises à l'échelle : la mise à l'échelle vit dans ``decision-engine`` comme
    pour les sept autres axes.

    ``all_repos_archived`` est le seul zéro légitime de cette chaîne. Il dit
    « on a regardé, tous les dépôts connus sont archivés ou forkés » — une
    observation, à distinguer d'une absence de mesure, qui reste ``None``.
    """

    event_type: Literal[EventType.DEVELOPER] = EventType.DEVELOPER
    symbol: str
    coin_id: str = Field(
        ...,
        description=(
            "CoinGecko id, e.g. 'aave' — joint directement Token.coin_id, comme "
            "FundamentalsEvent."
        ),
    )
    #: Dépôts retenus dans l'agrégat (archivés et forks exclus). Un décompte,
    #: pas une mesure. ``0`` est légal et n'accompagne qu'un cas :
    #: ``all_repos_archived=True``, où il dit « des dépôts existent, aucun n'est
    #: vivant ». Quand *aucun* dépôt n'a pu être lu, le collector ne publie pas
    #: d'événement du tout, plutôt qu'un événement à zéro. L'invariant est
    #: imposé à la construction et au décodage par ``_validate_repo_count``
    #: ci-dessous, pas seulement documenté : un événement qui le viole et
    #: atteint Redis deviendrait infalsifiable après coup.
    repo_count: int = Field(..., ge=0)
    #: commits sur 4 semaines / (médiane hebdomadaire sur 52 semaines x 4).
    #: 1.0 = rythme habituel. Borné en bas à 0 : c'est un rapport de comptages.
    commit_ratio_4w: float | None = Field(default=None, ge=0)
    pr_ratio_4w: float | None = Field(default=None, ge=0)
    #: Jours depuis le push le plus récent, tous dépôts confondus.
    days_since_push: int | None = Field(default=None, ge=0)
    #: Croissance des étoiles sur 7 jours, en fraction (0.02 = +2 %). Peut être
    #: négative : un dépôt perd des étoiles. Volontairement non bornée en bas,
    #: contrairement aux ratios ci-dessus : c'est un flux, pas un décompte, et
    #: un ``ge=0`` ajouté ici par excès de rigueur romprait la publication du
    #: token entier au premier repo en perte d'étoiles -- même incident que
    #: ``fees_24h_usd`` sur FundamentalsEvent.
    star_growth_pct_7d: float | None = None
    all_repos_archived: bool = False

    @model_validator(mode="after")
    def _validate_repo_count(self) -> DeveloperEvent:
        """Runs on construction *and* on decode (``parse_event`` /
        ``model_validate_json``), which is the half of this that matters: a
        malformed message from someone else's producer on the topic must be
        rejected too, not just a value we built ourselves.

        Does **not** run on ``model_copy(update=...)`` -- Pydantic skips
        validators on copy, so a collector patching a cached event that way
        (this service's two-clock republish is a standing invitation to)
        could still slip repo_count=0 past this check. See the same warning
        left on ``collector-binance-futures``'s ``long_short_ratio`` for the
        precedent this bit us on before.
        """
        if self.repo_count == 0 and not self.all_repos_archived:
            raise ValueError(
                "repo_count=0 requires all_repos_archived=True: a token with "
                'no live repos is a measurement ("we looked, all archived"), '
                "not the same as an event that was never emitted"
            )
        return self

    def partition_key(self) -> str:
        return self.symbol
