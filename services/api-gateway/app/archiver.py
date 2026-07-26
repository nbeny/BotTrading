"""Archive the raw broadcast stream, without interpreting it.

Deliberately separate from ``Persister``: that projects events into *business*
tables with a lifecycle (a RiskApprovedEvent becomes a Trade row that later gets
a fill price). This one stores events as they arrived so the Command Center feed
survives a page reload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cmi_common.db import EventMarket, EventSignal
from cmi_common.events import BaseEvent, DexEvent, PriceEvent, VolumeEvent
from cmi_common.events.journal import JournalEntryEvent

MARKET = EventMarket
SIGNAL = EventSignal

# High-volume, short-retention. Everything else worth keeping goes to SIGNAL.
_MARKET_TYPES = (PriceEvent, VolumeEvent, DexEvent)


def table_for(event: BaseEvent) -> type | None:
    """Which archive table, or None when the event must not be archived.

    The journal has its own table and a 180-day retention; archiving it too
    would duplicate the largest table in the system for no gain.

    An unrecognised type lands in SIGNAL rather than being dropped: an event we
    did not anticipate must stay visible, and the longer retention is the
    prudent default.
    """
    if isinstance(event, JournalEntryEvent):
        return None
    if isinstance(event, _MARKET_TYPES):
        return MARKET
    return SIGNAL


def _naive_utc(dt: datetime) -> datetime:
    """Naive UTC, matching persister._naive_utc. The columns are TIMESTAMPTZ and
    the session runs in UTC, so this is the repo-wide convention rather than a
    property of the column."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


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
        "time": _naive_utc(event.occurred_at),
        "event_id": event.event_id,
        "event_type": event_type_of(event),
        "topic": topic,
        "symbol": getattr(event, "symbol", None),
        "correlation_id": event.correlation_id,
        "payload": event.model_dump(mode="json"),
    }
