"""Collector source toggles + AI-quota status.

Reads/writes the Redis `collectors:runtime` key the poll loops honour, and reads
the `ai:quota:*` status the AI workers publish while paused on a usage limit.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import text

from cmi_common.auth import Principal
from cmi_common.events import AnalysisEvent
from cmi_common.kafka import Topic
from cmi_common.sources import (
    KNOWN_PLATFORMS,
    dedupe_channels,
    get_runtime,
    normalize_channel,
    set_runtime,
    source_status_key,
)

from ..auth_dep import require_principal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["collectors"])

_AI_WORKERS = ("ai-worker-haiku", "ai-worker-sonnet")
# Escalated signals with no decision for their correlation_id (in the payload).
_PENDING_WHERE = (
    "s.escalated AND NOT EXISTS (SELECT 1 FROM decisions d "
    "WHERE d.correlation_id = s.payload->>'correlation_id')"
)


#: One channel costs one MTProto call per cycle, and that call budget is what the
#: cap exists to bound — not the size of the JSON. The shipped seed is 24
#: channels, so this leaves the operator room to roughly double the desk before
#: anyone has to think about the poll interval.
MAX_TELEGRAM_CHANNELS = 50

#: Platforms probed for a health blob of their own. Derived from the canonical
#: list rather than enumerated here: a hardcoded tuple drops a new provider's
#: health silently — no error, no failing test, just a source that never reports
#: — which is the same drift CLAUDE.md documents for the three axis lists. Only
#: Telegram publishes today, so most of these are misses; that costs a dozen
#: Redis GETs per call, and `_source_status` already omits what is absent.
_STATUS_PLATFORMS = tuple(p for ps in KNOWN_PLATFORMS.values() for p in ps)


def _cache(request: Request):
    return request.app.state.cache


async def _source_status(cache: Any) -> dict[str, Any]:
    """Per-platform health, as published by the providers themselves.

    A platform that has never reported is *omitted*, not defaulted to healthy:
    "not measured" and "measured, fine" are different claims, and only the
    caller can render the difference once we stop erasing it here.
    """
    out: dict[str, Any] = {}
    for platform in _STATUS_PLATFORMS:
        status = await cache.get_json(source_status_key(platform))
        if status:
            out[platform] = status
    return out


class RuntimePatch(BaseModel):
    social_enabled: bool | None = None
    news_enabled: bool | None = None
    platforms: dict[str, bool] | None = None
    telegram_channels: list[str] | None = None

    @field_validator("telegram_channels")
    @classmethod
    def _normalize_channels(cls, value: list[str] | None) -> list[str] | None:
        # `None` is "not patched" and `[]` is "poll nobody" — an explicit clear
        # is a legitimate operator action, and the route's
        # `model_dump(exclude_none=True)` drops the former while carrying the
        # latter through to `set_runtime`, which replaces the list wholesale.
        if value is None:
            return None
        # Normalized through the same helper the provider resolves with, so what
        # the terminal shows back is exactly what gets polled. A rejected entry
        # raises here and FastAPI answers 422: unlike the env bootstrap, which
        # skips a bad handle, a request has a human behind it who can retype it.
        channels = dedupe_channels(normalize_channel(v) for v in value)
        # Counted after dedup, because duplicate mirrors cost no extra call.
        if len(channels) > MAX_TELEGRAM_CHANNELS:
            raise ValueError(f"at most {MAX_TELEGRAM_CHANNELS} channels")
        return channels


@router.get("/collectors/runtime")
async def get_collectors_runtime(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    cache = _cache(request)
    rt = await get_runtime(cache)
    return {
        **rt,
        "known_platforms": KNOWN_PLATFORMS,
        "source_status": await _source_status(cache),
    }


@router.post("/collectors/runtime")
async def set_collectors_runtime(
    body: RuntimePatch,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    cache = _cache(request)
    rt = await set_runtime(cache, body.model_dump(exclude_none=True))
    return {
        **rt,
        "known_platforms": KNOWN_PLATFORMS,
        "source_status": await _source_status(cache),
    }


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
    async with db._sessionmaker() as s:
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
            await s.execute(
                text(f"SELECT count(*) FROM signals s WHERE {_PENDING_WHERE}")
            )
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
    async with db._sessionmaker() as s:
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
        except Exception:
            continue
        await producer.publish(Topic.ANALYSIS, evt)
        requeued += 1
    logger.info("backfill re-queued %d escalated signals", requeued)
    return {"requeued": requeued, "candidates": len(rows)}
