"""Engine settings: read status + publish set_mode/kill/auto/caps commands."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal

_VALID_MODES = {"dry_run", "demo", "live"}
_CAPS_FIELDS = {
    "max_order_usd",
    "max_leverage",
    "max_orders_per_hour",
    "entry_timeout_s",
    "reconcile_interval_s",
}


class SettingsService:
    def __init__(self, publisher, reader) -> None:
        self._pub = publisher
        self._reader = reader

    async def status(self) -> dict[str, Any]:
        return await self._reader.settings()

    async def set_mode(self, mode: str, *, issued_by: str | None) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"invalid mode {mode}")
        await self._pub.publish(
            ControlCommand.SET_MODE, {"mode": mode}, issued_by=issued_by
        )

    async def set_kill_switch(self, enabled: bool, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.SET_KILL_SWITCH, {"enabled": enabled}, issued_by=issued_by
        )

    async def set_auto_trading(self, enabled: bool, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.SET_AUTO_TRADING, {"enabled": enabled}, issued_by=issued_by
        )

    async def set_caps(self, caps: dict[str, Any], *, issued_by: str | None) -> None:
        fields = {k: v for k, v in caps.items() if k in _CAPS_FIELDS and v is not None}
        await self._pub.publish(ControlCommand.SET_CAPS, fields, issued_by=issued_by)


router = APIRouter(prefix="/trading", tags=["settings"])


def _svc(request: Request) -> SettingsService:
    return request.app.state.settings_service


class ModeInput(BaseModel):
    mode: str


class EnabledInput(BaseModel):
    enabled: bool


class CapsInput(BaseModel):
    max_order_usd: float | None = None
    max_leverage: float | None = None
    max_orders_per_hour: int | None = None
    entry_timeout_s: int | None = None
    reconcile_interval_s: int | None = None


@router.get("/status")
async def status(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    return await _svc(request).status()


@router.post("/mode")
async def set_mode(
    body: ModeInput, request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    await _svc(request).set_mode(body.mode, issued_by=principal.sub)
    return {"ok": True, "mode": body.mode}


@router.post("/kill")
async def set_kill(
    body: EnabledInput,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    await _svc(request).set_kill_switch(body.enabled, issued_by=principal.sub)
    return {"ok": True, "trading_enabled": body.enabled}


@router.post("/auto")
async def set_auto(
    body: EnabledInput,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    await _svc(request).set_auto_trading(body.enabled, issued_by=principal.sub)
    return {"ok": True, "auto_trading_enabled": body.enabled}


@router.post("/caps")
async def set_caps(
    body: CapsInput, request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    await _svc(request).set_caps(
        body.model_dump(exclude_none=True), issued_by=principal.sub
    )
    return {"ok": True}
