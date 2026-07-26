"""Composite pagination cursor: (time, event_id).

A cursor on the timestamp alone skips or repeats rows as soon as two events
share a millisecond, which happens constantly -- 120 analyses per hour for a
single symbol were measured in production.

The two parts are separated by "|" rather than "_" because event ids routinely
contain underscores; a separator that depends on the id's alphabet corrupts
pagination silently the first time that assumption breaks.
"""

from __future__ import annotations

from datetime import datetime

SEP = "|"


def encode(time: datetime, event_id: str) -> str:
    return f"{time.isoformat()}{SEP}{event_id}"


def decode(cursor: str) -> tuple[datetime, str]:
    """Raises ValueError on anything malformed -- an error beats an arbitrary
    page, which would look like data rather than a bug."""
    raw_time, sep, event_id = cursor.partition(SEP)
    if not sep or not event_id:
        raise ValueError(f"malformed cursor: {cursor!r}")
    parsed = datetime.fromisoformat(raw_time)
    if parsed.tzinfo is None:
        raise ValueError(f"cursor time must be timezone-aware: {cursor!r}")
    return parsed, event_id
