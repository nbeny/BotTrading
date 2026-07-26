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


class News(Base, TimestampMixin):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(128))
    published_at: Mapped[int] = mapped_column(BigInteger)
    symbols: Mapped[list] = mapped_column(JSONB, default=list)
    provider_sentiment: Mapped[float | None] = mapped_column(Float)


class Sentiment(Base):
    __tablename__ = "sentiments"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    input_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    sentiment_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str] = mapped_column(String(128))


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


# Tables that become Timescale hypertables (time-partitioned).
# raw_content is deliberately excluded: its dedup needs UNIQUE(source, external_id)
# and Timescale requires the partitioning column in every unique index.
HYPERTABLES = {
    "prices": "time",
    "sentiments": "time",
    "signals": "time",
    "pipeline_rejections": "time",
}
