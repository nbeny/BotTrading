# services/control-api/app/routers/opportunities.py
"""Pending opportunities: list + approve/reject (human-in-the-loop)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class OpportunitiesService:
    def __init__(self, publisher, reader) -> None:
        self._pub = publisher
        self._reader = reader

    async def list(self) -> list[dict]:
        return await self._reader.pending()

    async def approve(self, event_id: str, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.APPROVE_OPPORTUNITY,
            {"event_id": event_id},
            issued_by=issued_by,
        )

    async def reject(
        self, event_id: str, *, reason: str | None, issued_by: str | None
    ) -> None:
        payload = {"event_id": event_id}
        if reason:
            payload["reason"] = reason
        await self._pub.publish(
            ControlCommand.REJECT_OPPORTUNITY, payload, issued_by=issued_by
        )


router = APIRouter(prefix="/trading/opportunities", tags=["opportunities"])


def _svc(request: Request) -> OpportunitiesService:
    return request.app.state.opportunities_service


class RejectInput(BaseModel):
    reason: str | None = None


@router.get("")
async def list_pending(
    request: Request, principal: Principal = Depends(require_principal)
) -> list[dict]:
    return await _svc(request).list()


@router.post("/{event_id}/approve")
async def approve(
    event_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    await _svc(request).approve(event_id, issued_by=principal.sub)
    return {"ok": True}


@router.post("/{event_id}/reject")
async def reject(
    event_id: str,
    request: Request,
    body: RejectInput | None = None,
    principal: Principal = Depends(require_principal),
) -> dict:
    await _svc(request).reject(
        event_id, reason=(body.reason if body else None), issued_by=principal.sub
    )
    return {"ok": True}
