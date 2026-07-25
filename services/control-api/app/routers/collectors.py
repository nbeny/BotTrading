"""Collector source toggles + AI-quota status.

Reads/writes the Redis `collectors:runtime` key the poll loops honour, and reads
the `ai:quota:*` status the AI workers publish while paused on a usage limit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.sources import KNOWN_PLATFORMS, get_runtime, set_runtime

from ..auth_dep import require_principal

router = APIRouter(tags=["collectors"])

_AI_WORKERS = ("ai-worker-haiku", "ai-worker-sonnet")


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
