import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module
from tests.test_control_api_settings import FakePublisher


class FakeReader:
    async def pending(self):
        return [{"event_id": "e1", "symbol": "SOL"}]


def test_list_pending() -> None:
    opp = load_module("routers.opportunities")
    svc = opp.OpportunitiesService(FakePublisher(), FakeReader())
    assert asyncio.run(svc.list())[0]["event_id"] == "e1"


def test_approve_and_reject_publish() -> None:
    opp = load_module("routers.opportunities")
    pub = FakePublisher()
    svc = opp.OpportunitiesService(pub, FakeReader())
    asyncio.run(svc.approve("e1", issued_by="admin"))
    asyncio.run(svc.reject("e2", reason="no", issued_by="admin"))
    assert (ControlCommand.APPROVE_OPPORTUNITY, {"event_id": "e1"}, "admin") in pub.calls
    assert (ControlCommand.REJECT_OPPORTUNITY, {"event_id": "e2", "reason": "no"}, "admin") in pub.calls
