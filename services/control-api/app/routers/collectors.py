"""Collector source toggles + AI-quota status.

Reads/writes the Redis `collectors:runtime` key the poll loops honour, and reads
the `ai:quota:*` status the AI workers publish while paused on a usage limit.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text

from cmi_common.auth import Principal
from cmi_common.events import AnalysisEvent
from cmi_common.kafka import Topic
from cmi_common.sources import KNOWN_PLATFORMS, get_runtime, set_runtime

from ..auth_dep import require_principal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["collectors"])

_AI_WORKERS = ("ai-worker-haiku", "ai-worker-sonnet")
# Escalated signals with no decision for their correlation_id (in the payload).
_PENDING_WHERE = (
    "s.escalated AND NOT EXISTS (SELECT 1 FROM decisions d "
    "WHERE d.correlation_id = s.payload->>'correlation_id')"
)


def _cache(request: Request):
    return request.app.state.cache


class RuntimePatch(BaseModel):
    social_enabled: bool | None = None
    news_enabled: bool | None = None
    platforms: dict[str, bool] | None = None


@router.get("/collectors/runtime")
async def get_collectors_runtime(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    rt = await get_runtime(_cache(request))
    return {**rt, "known_platforms": KNOWN_PLATFORMS}


@router.post("/collectors/runtime")
async def set_collectors_runtime(
    body: RuntimePatch,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    rt = await set_runtime(_cache(request), body.model_dump(exclude_none=True))
    return {**rt, "known_platforms": KNOWN_PLATFORMS}


@router.get("/systems/ai/quota")
async def ai_quota(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    cache = _cache(request)
    workers = []
    for svc in _AI_WORKERS:
        st = await cache.get_json(f"ai:quota:{svc}")
        if st:
            workers.append(st)
    paused = [w for w in workers if w.get("paused")]
    resume_at = max((w.get("resume_at", 0) for w in paused), default=None)
    return {"paused": bool(paused), "resume_at": resume_at, "workers": workers}


@router.get("/systems/coverage")
async def coverage(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    """How much of the pipeline is fully processed: sentiment scoring of raw
    content, and senior (Sonnet) decisions on escalated opportunities."""
    db = request.app.state.db
    async with db._sessionmaker() as s:  # noqa: SLF001
        sent = (
            await s.execute(
                text(
                    "SELECT count(*) AS total, "
                    "count(*) FILTER (WHERE scored_at IS NULL) AS unscored "
                    "FROM raw_content"
                )
            )
        ).one()
        pending = (
            await s.execute(text(f"SELECT count(*) FROM signals s WHERE {_PENDING_WHERE}"))
        ).scalar_one()
    return {
        "sentiment": {
            "total": sent.total,
            "scored": sent.total - sent.unscored,
            "unscored": sent.unscored,
        },
        "escalations": {"pending": pending},
    }


@router.post("/systems/backfill")
async def backfill(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    """Re-queue escalated-but-undecided opportunities from the last 24h back onto
    market.analysis.events so Sonnet can decide them (respecting its budget /
    cooldown). Catches opportunities missed during a quota or source outage."""
    db = request.app.state.db
    producer = request.app.state.producer
    async with db._sessionmaker() as s:  # noqa: SLF001
        rows = (
            await s.execute(
                text(
                    f"SELECT payload FROM signals s WHERE {_PENDING_WHERE} "
                    "AND s.time > (now() at time zone 'utc') - interval '24 hours' "
                    "ORDER BY s.time DESC LIMIT 200"
                )
            )
        ).all()
    requeued = 0
    for (payload,) in rows:
        try:
            evt = AnalysisEvent(**payload)
        except Exception:  # noqa: BLE001 - skip a malformed stored payload
            continue
        await producer.publish(Topic.ANALYSIS, evt)
        requeued += 1
    logger.info("backfill re-queued %d escalated signals", requeued)
    return {"requeued": requeued, "candidates": len(rows)}
