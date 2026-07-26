"""Journal aggregation: forward-price lookup plus pure summary shaping.

The shaping refuses to answer below a minimum sample. A mean over three
observations looks exactly like a mean over three hundred, and it invites
action -- so below the floor every comparison returns null instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Below this, every comparison returns null.
MIN_SAMPLE = 30
# Beyond this gap a stored price is not "the price at that instant" -- return
# nothing rather than something misleading, so a collector outage reads as an
# absence instead of an approximation.
PRICE_TOLERANCE = timedelta(minutes=10)
DEFAULT_HORIZONS = ("1h", "4h", "24h")


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _outcomes(rows: Iterable[dict], field: str = "pnl_net_pct") -> list[float]:
    """Rows without an outcome are excluded, never counted as zero -- an
    immature row is not a flat trade."""
    return [r[field] for r in rows if r.get(field) is not None]


def matured(rows: Iterable[dict], field: str) -> int:
    """Sample size **per horizon**. A row younger than 24h is usable at +1h and
    not at +24h; a global count would let a 24h analysis believe it had data."""
    return len(_outcomes(rows, field))


def compare_groups(
    group_a: Iterable[dict], group_b: Iterable[dict], field: str = "pnl_net_pct"
) -> dict[str, Any]:
    a, b = _outcomes(group_a, field), _outcomes(group_b, field)
    # Either side being thin suppresses the comparison: 300 against 4 is not a
    # comparison, however well-populated one side is.
    thin = len(a) < MIN_SAMPLE or len(b) < MIN_SAMPLE
    mean_a, mean_b = (None, None) if thin else (_mean(a), _mean(b))
    return {
        "n_a": len(a),
        "n_b": len(b),
        "mean_a": mean_a,
        "mean_b": mean_b,
        "delta": None if thin else round(mean_a - mean_b, 6),
        "insufficient_sample": thin,
    }


def by_cohort(
    rows: Iterable[dict], *, key: str, field: str = "pnl_net_pct"
) -> dict[str, dict[str, Any]]:
    """The minimum applies **per cohort**. Crossing axes fragments a thin sample
    fast, so a global floor would wave through cohorts of four.

    A row whose cohort key is missing is grouped under its stringified value
    rather than dropped -- silently discarding observations would understate
    every total.
    """
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row.get(key)), []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for name, bucket in buckets.items():
        values = _outcomes(bucket, field)
        enough = len(values) >= MIN_SAMPLE
        out[name] = {"n": len(values), "mean": _mean(values) if enough else None}
    return out


async def price_path(
    session: AsyncSession, symbol: str, start, horizon: str
) -> list[tuple[int, float]]:
    """Chronological (seconds_since_start, price) over [start, start+horizon].

    Feeds journal_sim.simulate_path, which walks the path rather than comparing
    endpoints.
    """
    rows = (
        await session.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM (time - :start))::int AS s, price_usd "
                "FROM prices WHERE symbol = :symbol "
                "AND time >= :start AND time <= :start + CAST(:horizon AS interval) "
                "ORDER BY time"
            ),
            {"symbol": symbol, "start": start, "horizon": horizon},
        )
    ).all()
    return [(int(s), float(p)) for s, p in rows]
