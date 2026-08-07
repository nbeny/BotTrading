"""Typed event schemas for the CMI platform.

All events share the :class:`BaseEvent` envelope and are discriminated by
``event_type``. Use :func:`parse_event` to decode a raw Kafka payload into the
correct concrete model.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import Field, TypeAdapter

from .account import AccountSnapshotEvent
from .analysis import AnalysisEvent
from .base import BaseEvent, EventType, Source
from .control import ControlCommand, ControlCommandEvent
from .decision import DecisionEvent, Direction
from .execution import ExecutionEvent, ExecutionKind
from .journal import JournalEntryEvent
from .market import (
    CandleEvent,
    DerivativesEvent,
    DeveloperEvent,
    DexEvent,
    FundamentalsEvent,
    PriceEvent,
    VolumeEvent,
)
from .news import NewsEvent
from .risk import RiskApprovedEvent, RiskRejectedEvent
from .sentiment import SentimentEvent
from .social import SocialEvent

# Discriminated union over the ``event_type`` literal field. Pydantic picks the
# right subclass automatically, giving us O(1) typed decoding.
#
# One member per line on purpose. An event missing from this union publishes
# perfectly and fails on *consumption* -- parse_event raises on the
# discriminator -- which is how JournalEntryEvent shipped broken. A single long
# line makes the one thing you have to check here uncheckable at a glance.
AnyEvent = Annotated[
    (
        PriceEvent
        | VolumeEvent
        | DexEvent
        | DerivativesEvent
        | FundamentalsEvent
        | DeveloperEvent
        | NewsEvent
        | SocialEvent
        | SentimentEvent
        | AnalysisEvent
        | DecisionEvent
        | RiskApprovedEvent
        | RiskRejectedEvent
        | ExecutionEvent
        | ControlCommandEvent
        | JournalEntryEvent
        | AccountSnapshotEvent
        | CandleEvent
    ),
    Field(discriminator="event_type"),
]

_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnyEvent)


def parse_event(raw: bytes | str | dict[str, Any]) -> BaseEvent:
    """Decode a Kafka value into the correct concrete event model."""
    if isinstance(raw, (bytes, str)):
        data = json.loads(raw)
    else:
        data = raw
    return _ADAPTER.validate_python(data)


__all__ = [
    "AccountSnapshotEvent",
    "AnalysisEvent",
    "AnyEvent",
    "BaseEvent",
    "CandleEvent",
    "ControlCommand",
    "ControlCommandEvent",
    "DecisionEvent",
    "DerivativesEvent",
    "DeveloperEvent",
    "DexEvent",
    "Direction",
    "EventType",
    "ExecutionEvent",
    "ExecutionKind",
    "FundamentalsEvent",
    "JournalEntryEvent",
    "NewsEvent",
    "PriceEvent",
    "RiskApprovedEvent",
    "RiskRejectedEvent",
    "SentimentEvent",
    "SocialEvent",
    "Source",
    "VolumeEvent",
    "parse_event",
]
