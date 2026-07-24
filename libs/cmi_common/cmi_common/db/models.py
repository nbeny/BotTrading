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
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class Decision(Base, TimestampMixin):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(256))
    url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    symbols: Mapped[list] = mapped_column(JSONB, default=list)
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
        UniqueConstraint("source", "external_id", name="uq_raw_content_source_external"),
        Index("ix_raw_content_unscored", "fetched_at",
              postgresql_where=sa_text("scored_at IS NULL")),
    )


class ContentSentimentAgg(Base):
    """Per-symbol/window rollup derived from scored raw_content."""

    __tablename__ = "content_sentiment_agg"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    window_size: Mapped[int] = mapped_column(Integer, primary_key=True)
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    unique_authors: Mapped[int] = mapped_column(Integer, default=0)
    engagement_sum: Mapped[float] = mapped_column(Float, default=0.0)
    avg_sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Tables that become Timescale hypertables (time-partitioned).
HYPERTABLES = {
    "prices": "time",
    "sentiments": "time",
    "signals": "time",
    "raw_content": "fetched_at",
}
