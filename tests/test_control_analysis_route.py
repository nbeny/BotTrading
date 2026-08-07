"""La route de scan publie une commande, elle n'écrit rien elle-même."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events.control import ControlCommand

analysis = load_service_module("control-api", "routers.analysis")


class _Publisher:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, command, payload, *, issued_by=None):
        self.published.append((command, payload, issued_by))


async def test_scan_request_publishes_the_command() -> None:
    pub = _Publisher()
    svc = analysis.AnalysisService(pub)
    await svc.request_threshold_scan(issued_by="operator@example.com")
    assert len(pub.published) == 1
    command, _payload, issued_by = pub.published[0]
    assert command == ControlCommand.RUN_THRESHOLD_SCAN
    assert issued_by == "operator@example.com"
