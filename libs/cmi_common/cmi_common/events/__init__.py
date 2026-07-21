"""Typed event schemas for the CMI platform.

All events share the :class:`BaseEvent` envelope and are discriminated by
``event_type``. Use :func:`parse_event` to decode a raw Kafka payload into the
correct concrete model.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Union

from pydantic import Field, TypeAdapter

from .analysis import AnalysisEvent
from .base import BaseEvent, EventType, Source
from .decision import DecisionEvent, Direction
from .execution import ExecutionEvent, ExecutionKind
from .market import DexEvent, PriceEvent, VolumeEvent
from .news import NewsEvent
from .risk import RiskApprovedEvent, RiskRejectedEvent
from .sentiment import SentimentEvent
from .social import SocialEvent

# Discriminated union over the ``event_type`` literal field. Pydantic picks the
# right subclass automatically, giving us O(1) typed decoding.
AnyEvent = Annotated[
    Union[
        PriceEvent,
        VolumeEvent,
        DexEvent,
        NewsEvent,
        SocialEvent,
        SentimentEvent,
        AnalysisEvent,
        DecisionEvent,
        RiskApprovedEvent,
        RiskRejectedEvent,
        ExecutionEvent,
    ],
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
    "AnalysisEvent",
    "AnyEvent",
    "BaseEvent",
    "DecisionEvent",
    "DexEvent",
    "Direction",
    "EventType",
    "ExecutionEvent",
    "ExecutionKind",
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
