"""Read-only summary of the counterfactual journal.

Every statistic carries its own sample size, and any comparison below the
minimum returns null. The `sample` block leads the response on purpose: a reader
should see how much data backs a number before the number itself.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .journal_calibration import MIN_N, TRIAGE_FACTORS, attribution, calibrate
from .journal_query import (
    MIN_SAMPLE,
    by_cohort,
    compare_groups,
    matured,
    price_path,
    utcnow,
)
from .journal_sim import simulate_path
from .routers import get_session_dep

logger = logging.getLogger(__name__)
router = APIRouter(tags=["journal"])

HORIZONS = tuple(
    h.strip()
    for h in os.getenv("COUNTERFACTUAL_HORIZONS", "1h,4h,24h").split(",")
    if h.strip()
)
_WINDOWS = {"7d": 7, "30d": 30, "90d": 90}
# Cohorts are reported for one horizon only -- see the comment at their use.
# Defaults to the middle horizon: a realistic short-term hold.
PRIMARY_HORIZON = os.getenv("COUNTERFACTUAL_PRIMARY_HORIZON", "4h")

_raw_threshold = os.getenv("RISK_MIN_SCORE")
#: None quand l'env n'est pas posee dans CE conteneur : afficher '-' plutot
#: qu'un defaut qui mentirait sur le seuil reellement applique par
#: risk-engine.
CURRENT_THRESHOLD: int | None = None
if _raw_threshold:
    try:
        CURRENT_THRESHOLD = int(_raw_threshold)
    except ValueError:
        logger.warning("RISK_MIN_SCORE=%r is not an int, ignoring", _raw_threshold)

_PNL_FIELD = f"pnl_{PRIMARY_HORIZON}"
_OUTCOME_FIELD = f"outcome_{PRIMARY_HORIZON}"

_ROWS_SQL = (
    "SELECT time, event_id, symbol, score, confidence, escalated, sonnet_called,"
    " sonnet_validated, sonnet_direction, entry_price, stop_loss, take_profit,"
    " factors, risk_verdict, decision_event_id, correlation_id"
    " FROM decision_journal WHERE time >= :since ORDER BY time DESC, event_id"
)

CACHE_TTL_S = 30.0


class _JudgedRowsCache:
    """Window-keyed TTL cache: the calibration slider re-applies its threshold
    in pure Python after loading, so every slider position for a given window
    should hit the same cached row set rather than re-running one price_path
    query per judged row on every request."""

    def __init__(self, ttl_s: float = CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._entries: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def fresh(self, window: str, now: float) -> list[dict[str, Any]] | None:
        entry = self._entries.get(window)
        if entry is None:
            return None
        at, rows = entry
        return list(rows) if now - at < self._ttl else None

    def put(self, window: str, now: float, rows: list[dict[str, Any]]) -> None:
        self._entries[window] = (now, rows)

    def clear(self) -> None:
        self._entries.clear()


JUDGED_CACHE = _JudgedRowsCache()


def attach_outcome(
    row: dict[str, Any], *, path: list[tuple[int, float]], horizon: str
) -> dict[str, Any]:
    """Attach a simulated outcome for one horizon, returning a new dict.

    A row with no entry levels never became a decision, so it has no P&L -- and
    emphatically not a P&L of zero, which would drag every average toward the
    middle and make a stalled pipeline look neutral.

    Returns a copy rather than mutating: this is called once per horizon on the
    same row, and mutating in place would leak one horizon's result into the
    next.
    """
    entry = row.get("entry_price")
    if not entry or row.get("stop_loss") is None or row.get("take_profit") is None:
        return {**row, f"pnl_{horizon}": None, f"outcome_{horizon}": None}
    result = simulate_path(
        entry=float(entry),
        # WATCH and LONG are sized the same side by the risk engine, so long is
        # the consistent default for a missing direction.
        direction=row.get("sonnet_direction") or "long",
        stop_loss=float(row["stop_loss"]),
        take_profit=float(row["take_profit"]),
        path=path,
    )
    return {
        **row,
        f"pnl_{horizon}": result.pnl_net_pct,
        f"outcome_{horizon}": result.outcome,
    }


@router.get("/systems/journal/summary")
async def journal_summary(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    result = await session.execute(
        text(
            "SELECT symbol, escalated, sonnet_called, sonnet_validated, "
            "       risk_verdict, risk_reason, confidence, dominant_factor, "
            "       dedup_trigger, market_cap_rank, "
            "       time, entry_price, stop_loss, take_profit, sonnet_direction "
            "FROM decision_journal WHERE time >= :since"
        ),
        {"since": since},
    )
    # `_mapping` needs no lint suppression: SLF001 is not in this repo's ruff
    # select list, so a suppression here would be inert and RUF100 would flag it.
    rows = [dict(r._mapping) for r in result.all()]

    # One price query per row per horizon. Correct and simple; measured in
    # task 11 before any batching is considered.
    for horizon in HORIZONS:
        rows = [
            attach_outcome(
                r,
                path=await price_path(session, r["symbol"], r["time"], horizon),
                horizon=horizon,
            )
            for r in rows
        ]

    escalated = [r for r in rows if r.get("escalated")]
    called = [r for r in escalated if r.get("sonnet_called")]
    validated = [r for r in called if r.get("sonnet_validated")]
    refused = [r for r in called if r.get("sonnet_validated") is False]
    approved = [r for r in rows if r.get("risk_verdict") == "approved"]
    rejected = [r for r in rows if r.get("risk_verdict") == "rejected"]

    return {
        "window": window,
        "horizons": list(HORIZONS),
        # Leads the response on purpose: how much data backs a number, before
        # the number.
        "sample": {
            "min_required": MIN_SAMPLE,
            "analyses": len(rows),
            "escalated": len(escalated),
            "sonnet_called": len(called),
            "validated": len(validated),
            "approved": len(approved),
            "matured": {h: matured(rows, f"pnl_{h}") for h in HORIZONS},
        },
        # Each question is answered per horizon, because each horizon asks a
        # different one: +1h whether the signal had immediate value, +4h a
        # realistic short-term hold, +24h whether it caught a broader move.
        # Collapsing them to a single number would discard that distinction.
        #
        # The field name matters: attach_outcome writes `pnl_<horizon>`, never
        # `pnl_net_pct`. Reading the default field here would report n=0 for
        # every question forever, whatever the data volume -- an endpoint that
        # looks alive and never answers.
        #
        # Q1 -- were the risk rejections right?
        "q1_rejected_vs_approved": {
            h: compare_groups(rejected, approved, field=f"pnl_{h}") for h in HORIZONS
        },
        # Q2 -- did the gate let value through? Confounded by construction: the
        # two populations differ before Sonnet ever intervenes.
        "q2_gate_discrimination": {
            h: compare_groups(
                [r for r in rows if not r.get("escalated")], escalated, field=f"pnl_{h}"
            )
            for h in HORIZONS
        },
        # Q3 -- the central question. Clean comparison: both groups passed the
        # same gate and saw the same analyst; only the verdict differs.
        "q3_sonnet_value": {
            h: compare_groups(validated, refused, field=f"pnl_{h}") for h in HORIZONS
        },
        # Cohorts use a single horizon: crossing three axes by three horizons
        # would emit nine blocks of mostly-null cells long before any of them
        # holds 30 observations.
        "cohorts": {
            "horizon": PRIMARY_HORIZON,
            "by_dominant_factor": by_cohort(
                rows, key="dominant_factor", field=f"pnl_{PRIMARY_HORIZON}"
            ),
            "by_dedup_trigger": by_cohort(
                rows, key="dedup_trigger", field=f"pnl_{PRIMARY_HORIZON}"
            ),
            "by_symbol": by_cohort(rows, key="symbol", field=f"pnl_{PRIMARY_HORIZON}"),
        },
        "updated_at": utcnow().isoformat(),
    }


async def _judged_rows(
    session: AsyncSession,
    since: datetime,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if limit is not None:
        stmt = text(_ROWS_SQL + " LIMIT :limit OFFSET :offset")
        params: dict[str, Any] = {"since": since, "limit": limit, "offset": offset}
    else:
        stmt = text(_ROWS_SQL)
        params = {"since": since}
    result = await session.execute(stmt, params)
    rows = [dict(r._mapping) for r in result.all()]
    judged: list[dict[str, Any]] = []
    for row in rows:
        if (
            row.get("entry_price")
            and row.get("stop_loss") is not None
            and row.get("take_profit") is not None
        ):
            path = await price_path(
                session, row["symbol"], row["time"], PRIMARY_HORIZON
            )
            judged.append(attach_outcome(row, path=path, horizon=PRIMARY_HORIZON))
        else:
            judged.append({**row, _PNL_FIELD: None, _OUTCOME_FIELD: None})
    return judged


async def _cached_judged_rows(
    session: AsyncSession, window: str, since: datetime
) -> list[dict[str, Any]]:
    """Full-window judged rows, shared by calibration and attribution.

    Both endpoints load the whole window and apply their own pure-Python
    aggregation on top (a threshold, a correlation) -- unlike `journal_decisions`,
    which is bounded/paginated and reads current data on every page.
    """
    now = time.monotonic()
    hit = JUDGED_CACHE.fresh(window, now)
    if hit is not None:
        return hit
    rows = await _judged_rows(session, since)
    JUDGED_CACHE.put(window, now, rows)
    return list(rows)


def _map_row(row: dict[str, Any]) -> dict[str, Any]:
    t = row.get("time")
    return {
        "time": t.isoformat() if t else None,
        "event_id": row["event_id"],
        "symbol": row["symbol"],
        "score": row["score"],
        "confidence": row["confidence"],
        "escalated": bool(row["escalated"]),
        "sonnet_called": bool(row["sonnet_called"]),
        "sonnet_validated": row["sonnet_validated"],
        "direction": row["sonnet_direction"],
        "passed": row["decision_event_id"] is not None,
        "risk_verdict": row["risk_verdict"],
        "pnl_pct": row[_PNL_FIELD],
        "outcome": row[_OUTCOME_FIELD],
        "correlation_id": row["correlation_id"],
    }


@router.get("/systems/journal/decisions")
async def journal_decisions(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    total_result = await session.execute(
        text("SELECT count(*) FROM decision_journal WHERE time >= :since"),
        {"since": since},
    )
    total = int(total_result.scalar_one() or 0)
    rows = await _judged_rows(session, since, limit=limit, offset=offset)
    return {
        "window": window,
        "horizon": PRIMARY_HORIZON,
        "current_threshold": CURRENT_THRESHOLD,
        "total": total,
        "rows": [_map_row(r) for r in rows],
    }


@router.get("/systems/journal/calibration")
async def journal_calibration(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    threshold: int = Query(70, ge=0, le=100),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    rows = await _cached_judged_rows(session, window, since)
    current = (
        calibrate(rows, threshold=CURRENT_THRESHOLD, field=_PNL_FIELD)
        if CURRENT_THRESHOLD is not None
        else None
    )
    return {
        "window": window,
        "horizon": PRIMARY_HORIZON,
        "min_n": MIN_N,
        "requested": calibrate(rows, threshold=threshold, field=_PNL_FIELD),
        "current": current,
    }


@router.get("/systems/journal/attribution")
async def journal_attribution(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    rows = await _cached_judged_rows(session, window, since)
    n = sum(1 for r in rows if r.get(_PNL_FIELD) is not None)
    return {
        "window": window,
        "horizon": PRIMARY_HORIZON,
        "n": n,
        "min_n": MIN_N,
        "sufficient": n >= MIN_N,
        "factors": attribution(rows, factor_keys=TRIAGE_FACTORS, field=_PNL_FIELD),
    }
