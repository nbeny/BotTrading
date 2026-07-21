"""Base event envelope shared by every event flowing through Kafka.

Every concrete event inherits from :class:`BaseEvent`, which provides a stable
envelope (id, type, timestamp, source, schema version, correlation id) so that
consumers can route, deduplicate and trace events without parsing the payload.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class EventType(str, Enum):
    """Discriminator used across the platform for typed dispatch."""

    PRICE = "PriceEvent"
    VOLUME = "VolumeEvent"
    DEX = "DexEvent"
    NEWS = "NewsEvent"
    SOCIAL = "SocialEvent"
    SENTIMENT = "SentimentEvent"
    ANALYSIS = "AnalysisEvent"
    DECISION = "DecisionEvent"
    RISK_APPROVED = "RiskApprovedEvent"
    RISK_REJECTED = "RiskRejectedEvent"
    EXECUTION = "ExecutionEvent"
    CONTROL_COMMAND = "ControlCommandEvent"


class Source(str, Enum):
    COINGECKO = "coingecko"
    DEXSCREENER = "dexscreener"
    CRYPTOCOMPARE = "cryptocompare"
    REDDIT = "reddit"
    TWITTER = "twitter"
    SENTIMENT_SERVICE = "sentiment-service"
    AI_HAIKU = "ai-worker-haiku"
    AI_SONNET = "ai-worker-sonnet"
    DECISION_ENGINE = "decision-engine"
    RISK_ENGINE = "risk-engine"
    TRADING_ENGINE = "trading-engine"
    CONTROL_API = "control-api"


class BaseEvent(BaseModel):
    """Common envelope for all events.

    The concrete ``event_type`` acts as a Pydantic discriminator so that a raw
    payload can be parsed into the correct subclass with :func:`parse_event`.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        use_enum_values=True,
        ser_json_timedelta="float",
    )

    event_id: str = Field(default_factory=_new_id)
    event_type: EventType
    schema_version: int = 1
    occurred_at: datetime = Field(default_factory=_utcnow)
    source: Source
    # Correlation id lets us trace a single token's opportunity end-to-end
    # (collector -> sentiment -> analysis -> decision -> risk).
    correlation_id: str = Field(default_factory=_new_id)
    # Optional free-form provenance metadata (api endpoint, cursor, etc.).
    meta: dict[str, Any] = Field(default_factory=dict)

    def as_kafka_value(self) -> bytes:
        """Serialize to compact JSON bytes for the Kafka value."""
        return self.model_dump_json().encode("utf-8")

    def partition_key(self) -> str:
        """Default partition key; subclasses key by symbol for ordering."""
        return self.event_id
