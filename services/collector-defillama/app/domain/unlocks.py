"""Reduce a DefiLlama emissions document to its next unlock.

The document is ~2.25 MB, almost all of it `documentedData` and chart series.
Everything this module needs is `metadata.events` (~8 KB) and
`supplyMetrics.maxSupply`; the rest is parsed by json and thrown away.

`events` is a full history, oldest first, reaching back to a protocol's first
distribution. Filtering to the future is therefore load-bearing: without it the
"next" unlock for Aave is one from December 2017.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Unlocks further out than this do not affect a trade being opened today.
HORIZON_DAYS = 30


@dataclass(frozen=True, slots=True)
class Unlock:
    at: datetime       # earliest contributing event in the window
    pct_supply: float  # percentage points of max supply, summed over the window


def next_unlock(
    document: dict[str, Any], *, now: datetime | None = None
) -> Unlock | None:
    """Total supply unlocking within the horizon, dated at its earliest event.

    Returns None when nothing is scheduled in the window — a measurement, and
    the good news.

    Raises ValueError when something *is* scheduled but cannot be sized, because
    the supply denominator is missing. Returning None there would be read one
    layer up as "we looked, nothing is coming": the client inserts the coin id
    on any non-raising call, the mapper turns that into
    ``has_unlock_schedule=True``, and the scorer turns *that* into a perfect
    fundamentals score. Raising leaves the key absent instead, so the axis
    reports unknown and is excluded from the score.

    A malformed document — a non-numeric timestamp, a null token count — raises
    for the same reason, and by design. Do not add a tolerant `except: continue`
    to this loop: it would convert "unknown" into "nothing is coming", which is
    the failure this whole module is shaped to avoid. The caller must let the
    exception leave the key absent.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    horizon = now + timedelta(days=HORIZON_DAYS)

    earliest: datetime | None = None
    tokens = 0.0
    for event in document.get("metadata", {}).get("events", []):
        raw_ts = event.get("timestamp")
        if raw_ts is None:
            continue
        at = datetime.fromtimestamp(int(raw_ts), tz=UTC)
        if not (now < at <= horizon):
            continue
        contributed = sum(float(n) for n in event.get("noOfTokens") or [])
        if contributed <= 0:
            # A zero-size marker must not date an unlock it did not cause.
            continue
        tokens += contributed
        if earliest is None or at < earliest:
            earliest = at

    if earliest is None:
        return None

    max_supply = float(document.get("supplyMetrics", {}).get("maxSupply") or 0.0)
    if max_supply <= 0:
        raise ValueError("unlock scheduled but maxSupply is missing or zero")
    return Unlock(at=earliest, pct_supply=round(100.0 * tokens / max_supply, 4))
