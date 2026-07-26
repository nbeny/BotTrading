"""Canonical Kafka topic names and their event bindings."""

from __future__ import annotations

from enum import Enum

from ..events.analysis import AnalysisEvent
from ..events.control import ControlCommandEvent
from ..events.decision import DecisionEvent
from ..events.execution import ExecutionEvent
from ..events.journal import JournalEntryEvent
from ..events.market import DexEvent, PriceEvent, VolumeEvent
from ..events.news import NewsEvent
from ..events.risk import RiskApprovedEvent
from ..events.sentiment import SentimentEvent
from ..events.social import SocialEvent


class Topic(str, Enum):
    PRICE = "market.price.events"
    VOLUME = "market.volume.events"
    DEX = "market.dex.events"
    NEWS = "market.news.events"
    SOCIAL = "market.social.events"
    SENTIMENT = "market.sentiment.events"
    ANALYSIS = "market.analysis.events"
    DECISION = "decision.events"
    RISK_APPROVED = "risk.approved.events"
    EXECUTION = "execution.events"
    CONTROL = "control.commands"
    JOURNAL = "journal.entries"


# Which event type is expected on each topic (for validation / documentation).
TOPIC_EVENT = {
    Topic.PRICE: PriceEvent,
    Topic.VOLUME: VolumeEvent,
    Topic.DEX: DexEvent,
    Topic.NEWS: NewsEvent,
    Topic.SOCIAL: SocialEvent,
    Topic.SENTIMENT: SentimentEvent,
    Topic.ANALYSIS: AnalysisEvent,
    Topic.DECISION: DecisionEvent,
    Topic.RISK_APPROVED: RiskApprovedEvent,
    Topic.EXECUTION: ExecutionEvent,
    Topic.CONTROL: ControlCommandEvent,
    Topic.JOURNAL: JournalEntryEvent,
}

# Recommended partition counts (see docs/scaling.md). High-fan-in topics get
# more partitions so consumer groups can scale horizontally.
TOPIC_PARTITIONS = {
    Topic.PRICE: 12,
    Topic.VOLUME: 6,
    Topic.DEX: 12,
    Topic.NEWS: 6,
    Topic.SOCIAL: 6,
    Topic.SENTIMENT: 6,
    Topic.ANALYSIS: 6,
    Topic.DECISION: 3,
    Topic.RISK_APPROVED: 3,
    Topic.EXECUTION: 3,
    Topic.CONTROL: 3,
    Topic.JOURNAL: 6,
}
