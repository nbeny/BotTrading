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
    at: datetime
    pct_supply: float


def next_unlock(
    document: dict[str, Any], *, now: datetime | None = None
) -> Unlock | None:
    """Total supply unlocking within the horizon, dated at its earliest event.

    Returns None when nothing is scheduled in the window, and also when the
    supply denominator is missing — an unlock whose size cannot be expressed as
    a fraction of supply is not a measurement, and must not be served as one.
    """
    now = now or datetime.now(tz=UTC)
    horizon = now + timedelta(days=HORIZON_DAYS)

    max_supply = float(document.get("supplyMetrics", {}).get("maxSupply") or 0.0)
    if max_supply <= 0:
        return None

    earliest: datetime | None = None
    tokens = 0.0
    for event in document.get("metadata", {}).get("events", []):
        raw_ts = event.get("timestamp")
        if raw_ts is None:
            continue
        at = datetime.fromtimestamp(int(raw_ts), tz=UTC)
        if not (now < at <= horizon):
            continue
        tokens += sum(float(n) for n in event.get("noOfTokens") or [])
        if earliest is None or at < earliest:
            earliest = at

    if earliest is None or tokens <= 0:
        return None
    return Unlock(at=earliest, pct_supply=round(100.0 * tokens / max_supply, 4))
