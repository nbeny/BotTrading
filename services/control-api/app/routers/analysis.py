"""Demande de scan de calibration (publiée comme RUN_THRESHOLD_SCAN)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class AnalysisService:
    def __init__(self, publisher) -> None:
        self._pub = publisher

    async def request_threshold_scan(self, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.RUN_THRESHOLD_SCAN, {}, issued_by=issued_by
        )


router = APIRouter(prefix="/analysis", tags=["analysis"])


def _svc(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


@router.post("/threshold-scan")
async def request_threshold_scan(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    """Demande un scan. Le job décide s'il tourne : une demande pendant un scan
    en cours est ignorée côté decision-engine, ce n'est pas une erreur ici."""
    await _svc(request).request_threshold_scan(issued_by=principal.sub)
    return {"ok": True}
