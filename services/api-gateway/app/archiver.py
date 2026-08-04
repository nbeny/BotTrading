"""Archive the raw broadcast stream, without interpreting it.

Deliberately separate from ``Persister``: that projects events into *business*
tables with a lifecycle (a RiskApprovedEvent becomes a Trade row that later gets
a fill price). This one stores events as they arrived so the Command Center feed
survives a page reload.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from cmi_common.db import Database, EventMarket, EventSignal
from cmi_common.events import (
    AnalysisEvent,
    BaseEvent,
    DexEvent,
    PriceEvent,
    VolumeEvent,
)
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent
from cmi_common.kafka import TOPIC_EVENT, Topic
from cmi_common.observability import EVENTS_CONSUMED

logger = logging.getLogger(__name__)
# Not plain "api-gateway": the persister in this same process already increments
# EVENTS_CONSUMED under that label, and the two consumers overlap on five of the
# eight topics. Sharing the label would have doubled the counter for price,
# analysis, decision, risk-approved and execution events -- silently, in every
# dashboard built on it. The archive gets its own series instead.
SERVICE = "api-gateway-archiver"

# Reverse lookup so a row records which topic carried it. Built once.
# TOPIC_EVENT is one class per topic, so the inversion is lossless.
_TOPIC_BY_TYPE: dict[type, str] = {
    cls: topic.value for topic, cls in TOPIC_EVENT.items()
}
# RiskRejectedEvent has no topic of its own: both decision-engine and risk-engine
# publish it on the decision topic as an audit trail. Without this it would be
# archived with an empty topic and drop out of any topic-filtered feed.
_TOPIC_BY_TYPE[RiskRejectedEvent] = Topic.DECISION.value

MARKET = EventMarket
SIGNAL = EventSignal

# High-volume, short-retention. Everything else worth keeping goes to SIGNAL.
_MARKET_TYPES = (PriceEvent, VolumeEvent, DexEvent)

# Events that already have a dedicated table with its own retention. Archiving
# them again duplicates the largest tables in the system for no gain:
#
#   JournalEntryEvent  -> decision_journal    (180 days)
#   AnalysisEvent      -> signals             (persister._save_signal)
#   RiskRejectedEvent  -> pipeline_rejections (persister._save_rejection)
#
# Measured in production 8 hours after the archive shipped: events_signal held
# 179467 RiskRejectedEvent and 179002 AnalysisEvent against 427 sentiments and
# 26 decisions -- 99.8% duplication, 409 MB, on a 90-day retention that would
# have reached ~110 GB on a VPS with 4.5 GB free. The tier was designed around
# "signal events are low-volume"; these two are not, and they were never the
# ones worth re-reading here because they are already queryable elsewhere.
_ALREADY_PERSISTED = (JournalEntryEvent, AnalysisEvent, RiskRejectedEvent)


def table_for(event: BaseEvent) -> type | None:
    """Which archive table, or None when the event must not be archived.

    An unrecognised type lands in SIGNAL rather than being dropped: an event we
    did not anticipate must stay visible, and the longer retention is the
    prudent default.
    """
    if isinstance(event, _ALREADY_PERSISTED):
        return None
    if isinstance(event, _MARKET_TYPES):
        return MARKET
    return SIGNAL


def event_type_of(event: BaseEvent) -> str:
    """The event type as a plain ``str``.

    ``BaseEvent`` sets ``use_enum_values=True``, but that only applies to
    *validated* input -- the default never passes through validation, so
    ``event.event_type`` is still an ``EventType`` member. It compares equal to
    ``"PriceEvent"`` because the enum mixes in ``str``, which is exactly what
    makes the trap quiet: ``str()`` and f-strings on it yield
    ``"EventType.PRICE"`` since Python 3.11. That string would reach Prometheus
    label values and the archived row, splitting one event type into two names.
    """
    return str(getattr(event.event_type, "value", event.event_type))


def to_row(event: BaseEvent, *, topic: str) -> dict[str, Any]:
    """Indexed columns are lifted out for querying; the whole event is kept in
    `payload` so nothing is lost to a schema that did not anticipate it."""
    return {
        "time": event.occurred_at,
        "event_id": event.event_id,
        "event_type": event_type_of(event),
        "topic": topic,
        "symbol": getattr(event, "symbol", None),
        "correlation_id": event.correlation_id,
        "payload": event.model_dump(mode="json"),
    }


class EventArchiver:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def handle(self, event: BaseEvent) -> None:
        table = table_for(event)
        if table is None:
            return
        topic = _TOPIC_BY_TYPE.get(type(event), "")
        try:
            async with self._db.sessionmaker() as s:
                await s.execute(
                    insert(table)
                    .values(**to_row(event, topic=topic))
                    .on_conflict_do_nothing()
                )
                await s.commit()
            EVENTS_CONSUMED.labels(SERVICE, topic, event_type_of(event)).inc()
        except Exception:
            # The archive is observability. A write failure must not kill the
            # shared Kafka consumer and lose business events with it.
            logger.warning(
                "archive write failed for %s", event_type_of(event), exc_info=True
            )
