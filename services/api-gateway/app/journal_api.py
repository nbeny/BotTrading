"""Read-only summary of the counterfactual journal.

Every statistic carries its own sample size, and any comparison below the
minimum returns null. The `sample` block leads the response on purpose: a reader
should see how much data backs a number before the number itself.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .journal_query import MIN_SAMPLE, by_cohort, compare_groups, matured, utcnow
from .routers import get_session_dep

router = APIRouter(tags=["journal"])

HORIZONS = tuple(
    h.strip()
    for h in os.getenv("COUNTERFACTUAL_HORIZONS", "1h,4h,24h").split(",")
    if h.strip()
)
_WINDOWS = {"7d": 7, "30d": 30, "90d": 90}


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
            "       dedup_trigger, market_cap_rank "
            "FROM decision_journal WHERE time >= :since"
        ),
        {"since": since},
    )
    # `_mapping` needs no lint suppression: SLF001 is not in this repo's ruff
    # select list, so a suppression here would be inert and RUF100 would flag it.
    rows = [dict(r._mapping) for r in result.all()]

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
        # Q1 -- were the risk rejections right?
        "q1_rejected_vs_approved": compare_groups(rejected, approved),
        # Q2 -- did the gate let value through? Confounded by construction: the
        # two populations differ before Sonnet ever intervenes.
        "q2_gate_discrimination": compare_groups(
            [r for r in rows if not r.get("escalated")], escalated
        ),
        # Q3 -- the central question. Clean comparison: both groups passed the
        # same gate and saw the same analyst; only the verdict differs.
        "q3_sonnet_value": compare_groups(validated, refused),
        "cohorts": {
            "by_dominant_factor": by_cohort(rows, key="dominant_factor"),
            "by_dedup_trigger": by_cohort(rows, key="dedup_trigger"),
            "by_symbol": by_cohort(rows, key="symbol"),
        },
        "updated_at": utcnow().isoformat(),
    }
