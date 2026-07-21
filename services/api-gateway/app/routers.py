"""Read-only REST endpoints exposing platform state."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db import Decision, Signal, Trade

router = APIRouter(prefix="/api/v1", tags=["intelligence"])


def get_session_dep():
    # Bound in main.py to the service's Database instance.
    raise NotImplementedError


@router.get("/opportunities")
async def opportunities(
    limit: int = Query(50, ge=1, le=500),
    min_score: int = Query(0, ge=0, le=100),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    """Latest analysis signals, highest opportunity first."""
    stmt = (
        select(Signal)
        .where(Signal.opportunity_score >= min_score)
        .order_by(desc(Signal.time))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "symbol": r.symbol,
            "opportunity_score": r.opportunity_score,
            "confidence": r.confidence,
            "reason": r.reason,
            "escalated": r.escalated,
            "time": r.time,
        }
        for r in rows
    ]


@router.get("/decisions")
async def decisions(
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(Decision).order_by(desc(Decision.created_at)).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "symbol": r.symbol,
            "direction": r.direction,
            "opportunity_score": r.opportunity_score,
            "confidence": r.confidence,
            "ai_validated": r.ai_validated,
            "rationale": r.rationale,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/trades")
async def trades(
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    """Risk-approved actionable signals."""
    stmt = select(Trade).order_by(desc(Trade.created_at)).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "symbol": r.symbol,
            "direction": r.direction,
            "entry_price": r.entry_price,
            "stop_loss": r.stop_loss,
            "take_profit": r.take_profit,
            "confidence": r.confidence,
            "position_size_pct": r.position_size_pct,
            "risk_reward_ratio": r.risk_reward_ratio,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in rows
    ]
