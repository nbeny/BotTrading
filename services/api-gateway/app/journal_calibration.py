"""Pure math for the /journal panels. No I/O, no SQL.

MIN_N guards every statistic: below it the value is None (rendered '—'), not
a confident number computed on three points.
"""

from __future__ import annotations

import math
from typing import Any, TypeGuard

MIN_N = 20

TRIAGE_FACTORS: tuple[str, ...] = ("momentum", "volume", "sentiment", "liquidity")


def _is_num(v: object) -> TypeGuard[int | float]:
    """`bool` is an `int` subclass — exclude it, or `True` parses as 1.0."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def calibrate(
    rows: list[dict[str, Any]], *, threshold: int, field: str
) -> dict[str, Any]:
    eligible = [r for r in rows if r.get("score") is not None]
    selected = [r for r in eligible if r["score"] >= threshold]
    judged = [r for r in selected if _is_num(r.get(field))]
    n = len(judged)
    out: dict[str, Any] = {
        "threshold": threshold,
        "selected": len(selected),
        "judged": n,
        # Gates on judged, not selected — young rows have no pnl yet.
        "sufficient": n >= MIN_N,
        "win_rate": None,
        "avg_pnl_pct": None,
        "total_pnl_pct": None,
    }
    if n >= MIN_N:
        pnls = [float(r[field]) for r in judged]
        wins = sum(1 for p in pnls if p > 0)
        out["win_rate"] = round(wins / n, 4)
        out["avg_pnl_pct"] = round(sum(pnls) / n, 4)
        out["total_pnl_pct"] = round(sum(pnls), 4)
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Stdlib `statistics.correlation` raises where we need `None`."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def attribution(
    rows: list[dict[str, Any]], *, factor_keys: tuple[str, ...], field: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in factor_keys:
        pairs = [
            (float((r.get("factors") or {})[key]), float(r[field]))
            for r in rows
            if _is_num((r.get("factors") or {}).get(key)) and _is_num(r.get(field))
        ]
        r_val = (
            pearson([p[0] for p in pairs], [p[1] for p in pairs])
            if len(pairs) >= MIN_N
            else None
        )
        out.append(
            {
                "key": key,
                "n": len(pairs),
                "correlation": None if r_val is None else round(r_val, 4),
            }
        )
    return out
