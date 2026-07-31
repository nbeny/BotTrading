"""ORM models mapping the CMI domain to PostgreSQL/TimescaleDB.

Time-series heavy tables (``prices``, ``signals``) are turned into Timescale
hypertables in the Alembic migration; here they are plain SQLAlchemy models.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Token(Base, TimestampMixin):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    coin_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    name: Mapped[str | None] = mapped_column(String(128))
    chain: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(128), index=True)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (UniqueConstraint("symbol", "chain", name="symbol_chain"),)


class Price(Base):
    """Time-series price points -> Timescale hypertable on ``time``."""

    __tablename__ = "prices"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 2))
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 2))
    price_change_pct_24h: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))

    __table_args__ = (Index("ix_prices_symbol_time", "symbol", "time"),)


class Signal(Base):
    """Intermediate opportunity signals (analysis outputs) -> hypertable."""

    __tablename__ = "signals"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_score: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str] = mapped_column(String(32), default="unknown")
    factors_present: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class PipelineRejection(Base):
    """Where a signal died, per stage -> hypertable on ``time``.

    "Why do I get no decisions?" is only answerable if every stage records its
    refusals. Sourced from RiskRejectedEvent, which both the decision engine and
    the risk engine publish on the decision topic.
    """

    __tablename__ = "pipeline_rejections"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stage: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    correlation_id: Mapped[str | None] = mapped_column(String(64), default=None)
    reason: Mapped[str] = mapped_column(Text)


class DecisionJournal(Base):
    """One row per analysis — escalated or not -> hypertable on ``time``.

    The non-escalated rows are the control group. Without them "would this
    signal have deserved an analysis?" is unanswerable, because the only
    observable population would be the one the gate already selected.
    """

    __tablename__ = "decision_journal"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    signal_event_id: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(64))

    factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
    score: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    factors_present: Mapped[int] = mapped_column(SmallInteger)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    volatility_1h: Mapped[float | None] = mapped_column(Float, default=None)
    volatility_24h: Mapped[float | None] = mapped_column(Float, default=None)
    dominant_factor: Mapped[str | None] = mapped_column(String(16), default=None)
    dominant_factor_share: Mapped[float | None] = mapped_column(Float, default=None)
    market_cap_rank: Mapped[int | None] = mapped_column(Integer, default=None)

    sonnet_called: Mapped[bool] = mapped_column(Boolean, default=False)
    sonnet_validated: Mapped[bool | None] = mapped_column(Boolean, default=None)
    sonnet_score: Mapped[int | None] = mapped_column(Integer, default=None)
    sonnet_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    sonnet_direction: Mapped[str | None] = mapped_column(String(8), default=None)
    skip_reason: Mapped[str | None] = mapped_column(String(64), default=None)

    cooldown_verdict: Mapped[bool | None] = mapped_column(Boolean, default=None)
    dedup_verdict: Mapped[bool | None] = mapped_column(Boolean, default=None)
    dedup_trigger: Mapped[str | None] = mapped_column(String(8), default=None)
    drift_momentum: Mapped[float | None] = mapped_column(Float, default=None)
    drift_volume: Mapped[float | None] = mapped_column(Float, default=None)
    drift_sentiment: Mapped[float | None] = mapped_column(Float, default=None)
    drift_liquidity: Mapped[float | None] = mapped_column(Float, default=None)
    sign_flip_chg: Mapped[bool | None] = mapped_column(Boolean, default=None)
    sign_flip_sentiment: Mapped[bool | None] = mapped_column(Boolean, default=None)
    score_anchor: Mapped[int | None] = mapped_column(Integer, default=None)
    factors_present_anchor: Mapped[int | None] = mapped_column(
        SmallInteger, default=None
    )
    seconds_since_anchor: Mapped[int | None] = mapped_column(Integer, default=None)
    regime: Mapped[str | None] = mapped_column(String(16), default=None)
    regime_anchor: Mapped[str | None] = mapped_column(String(16), default=None)
    dedup_version: Mapped[str | None] = mapped_column(String(32), default=None)
    dedup_quantile: Mapped[float | None] = mapped_column(Float, default=None)
    dedup_deltas: Mapped[dict] = mapped_column(JSONB, default=dict)

    decision_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    risk_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    risk_verdict: Mapped[str | None] = mapped_column(String(16), default=None)
    risk_reason: Mapped[str | None] = mapped_column(Text, default=None)
    execution_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    execution_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)


class _EventArchiveMixin:
    """Raw broadcast-stream archive. Two tables share this shape and differ only
    in retention: TimescaleDB drops whole chunks by time and cannot filter by
    event type, so differentiated retention requires separate hypertables."""

    # timezone=True to match what migrations 0010/0011 actually create. Without
    # it the ORM believes the column is TIMESTAMP WITHOUT TIME ZONE, so the
    # create_all in tests/test_sentiment_reader_sql.py would build a table that
    # disagrees with production. Several older models in this file still carry
    # that mismatch; it is not fixed here because changing them is a separate
    # change with its own blast radius.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(64), default=None)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class EventMarket(Base, _EventArchiveMixin):
    """Price, volume and dex events -- high volume, 7-day retention."""

    __tablename__ = "events_market"


class EventSignal(Base, _EventArchiveMixin):
    """Sentiment, analysis, decision, risk and execution events -- low volume,
    90-day retention, because these are the ones worth looking back at."""

    __tablename__ = "events_signal"


class Decision(Base, TimestampMixin):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    opportunity_score: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    ai_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    rationale: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (Index("ix_decisions_created_at", "created_at"),)


class Trade(Base, TimestampMixin):
    """Risk-approved actionable signals (fed to the external trading engine)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    position_size_pct: Mapped[float] = mapped_column(Float, default=0.0)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    # Lifecycle: approved -> submitted -> filled -> closed / failed / rejected
    status: Mapped[str] = mapped_column(String(16), default="approved")
    kraken_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    pnl: Mapped[float | None] = mapped_column(Float, default=None)

    decision: Mapped[Decision | None] = relationship()

    __table_args__ = (Index("ix_trades_created_at", "created_at"),)


class RawContent(Base):
    """One ingested social post or news article; scored asynchronously."""

    __tablename__ = "raw_content"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(256))
    url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    symbols: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    engagement: Mapped[float | None] = mapped_column(Float)
    lang: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    sentiment_model: Mapped[str | None] = mapped_column(String(128))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "source", "external_id", name="uq_raw_content_source_external"
        ),
        Index("ix_raw_content_unscored", "fetched_at",
              postgresql_where=sa_text("scored_at IS NULL")),
        Index("ix_raw_content_fetched_at", "fetched_at"),
        # The existing ix_raw_content_unscored is partial (WHERE scored_at IS
        # NULL) and cannot serve a `scored_at >= W` range scan.
        Index("ix_raw_content_scored_at", "scored_at"),
    )


class ServiceHealth(Base):
    """Latest health probe per service (written by the health collector)."""

    __tablename__ = "service_health"

    service: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="healthy")
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AccountSnapshot(Base):
    """One venue's balance at one instant, as published by trading-engine.

    Deliberately not a hypertable: one row per venue per minute is 1440 rows a
    day for one venue, and the only query is "the newest snapshot", which time
    partitioning would complicate rather than help. Migration 0012 indexes
    ``fetched_at DESC`` for exactly that query -- see the note there on why it
    is not a (venue, fetched_at) composite yet.
    """

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Unique because ON CONFLICT DO NOTHING infers on it: Kafka is at-least-once
    # and a redelivered message carries an identical event.
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    venue: Mapped[str] = mapped_column(String(32))
    equity_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    cash_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    balances: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # timezone=True to match what migration 0012 creates.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_account_snapshots_venue_time", "venue", sa_text("fetched_at DESC")),
    )


class ContentSentimentAgg(Base):
    """Per-symbol hourly rollup derived from scored raw_content.

    All stored quantities are additive so any trailing window is derived by
    summing the covering buckets; means are computed once at read time. The key
    is (symbol, kind, bucket_start) at a single hourly resolution.
    """

    __tablename__ = "content_sentiment_agg"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_sum: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    engagement_sum: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ContentSentimentAggDaily(Base):
    """Daily rollup of aged-out hourly buckets (same additive columns).

    A compaction loop rolls hourly buckets older than the retention window into
    one daily bucket per (symbol, kind, day) and deletes the compacted hourly
    rows in the same transaction, so a given day lives in exactly one of the two
    tables — never both. Long-window reads union the two; short windows (<= the
    retention) never reach this table.
    """

    __tablename__ = "content_sentiment_agg_daily"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_sum: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    engagement_sum: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Candle(Base):
    """OHLC candles from the execution venue (Kraken spot).

    `interval` is part of the key so one table serves both granularities; the
    forming candle is rewritten on every sweep, so writers upsert with
    ON CONFLICT DO UPDATE. Whether a candle is closed is derived from its
    timestamp, never stored — a boolean column lies the moment a writer forgets it.
    """

    __tablename__ = "candles"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    high: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    low: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    close: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 12), default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="kraken")


class MarketDepth(Base):
    """Order-book snapshot: the measured liquidity that replaces the volume proxy."""

    __tablename__ = "market_depth"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    mid_price: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    spread_pct: Mapped[float] = mapped_column(Float)
    bid_depth_usd: Mapped[Decimal] = mapped_column(Numeric(38, 2))
    ask_depth_usd: Mapped[Decimal] = mapped_column(Numeric(38, 2))
    source: Mapped[str] = mapped_column(String(32), default="kraken")


class VenuePair(Base):
    """Which symbols are actually tradable on which venue, and at what minimum.

    Reference data, not a time series. `ambiguous` records that the CoinGecko
    ticker resolved to more than one coin: tickers are not unique there, and
    attaching a real Kraken pair's candles to a worthless homonym is a silent
    correctness bug, so the ambiguity is stored rather than swallowed.
    """

    __tablename__ = "venue_pairs"

    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    pair: Mapped[str] = mapped_column(String(64))
    ordermin: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Tables that become Timescale hypertables (time-partitioned).
# raw_content is deliberately excluded: its dedup needs UNIQUE(source, external_id)
# and Timescale requires the partitioning column in every unique index.
HYPERTABLES = {
    "prices": "time",
    "sentiments": "time",
    "signals": "time",
    "pipeline_rejections": "time",
    "decision_journal": "time",
    "events_market": "time",
    "events_signal": "time",
}
