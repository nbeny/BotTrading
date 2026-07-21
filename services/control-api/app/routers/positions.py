# services/control-api/app/routers/positions.py
"""Positions: list live positions + publish close / adjust-SLTP commands."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class PositionsService:
    def __init__(self, publisher, reader) -> None:
        self._pub = publisher
        self._reader = reader

    async def list(self) -> list[dict]:
        return await self._reader.positions()

    async def close(self, event_id: str, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.CLOSE_POSITION, {"event_id": event_id}, issued_by=issued_by
        )

    async def adjust(self, event_id: str, *, stop_loss, take_profit, issued_by: str | None) -> None:
        payload = {"event_id": event_id}
        if stop_loss is not None:
            payload["stop_loss"] = stop_loss
        if take_profit is not None:
            payload["take_profit"] = take_profit
        await self._pub.publish(ControlCommand.ADJUST_SLTP, payload, issued_by=issued_by)


router = APIRouter(prefix="/trading/positions", tags=["positions"])


def _svc(request: Request) -> PositionsService:
    return request.app.state.positions_service


class SlTpInput(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


@router.get("")
async def list_positions(request: Request,
                         principal: Principal = Depends(require_principal)) -> list[dict]:
    return await _svc(request).list()


@router.post("/{event_id}/close")
async def close(event_id: str, request: Request,
                principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).close(event_id, issued_by=principal.sub)
    return {"ok": True}


@router.patch("/{event_id}/sltp")
async def adjust(event_id: str, body: SlTpInput, request: Request,
                 principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).adjust(event_id, stop_loss=body.stop_loss,
                               take_profit=body.take_profit, issued_by=principal.sub)
    return {"ok": True}
