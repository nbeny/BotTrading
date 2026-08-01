# services/control-api/app/routers/orders.py
"""Manual order placement (published as a MANUAL_ORDER command)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class OrdersService:
    def __init__(self, publisher) -> None:
        self._pub = publisher

    async def place(self, order: dict, *, issued_by: str | None) -> None:
        await self._pub.publish(ControlCommand.MANUAL_ORDER, order, issued_by=issued_by)


router = APIRouter(prefix="/trading/orders", tags=["orders"])


def _svc(request: Request) -> OrdersService:
    return request.app.state.orders_service


class OrderInput(BaseModel):
    symbol: str
    side: str  # buy | sell
    order_type: str  # market | limit
    quantity: float
    price: float | None = None


@router.post("")
async def place_order(
    body: OrderInput,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    await _svc(request).place(
        body.model_dump(exclude_none=True), issued_by=principal.sub
    )
    return {"ok": True}
