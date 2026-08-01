# tests/test_control_api_positions.py
import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module
from tests.test_control_api_settings import FakePublisher


class FakeReader:
    async def positions(self):
        return [{"event_id": "e1", "symbol": "SOL", "size": 2.0}]


def _svc():
    positions = load_module("routers.positions")
    return positions, positions.PositionsService(FakePublisher(), FakeReader())


def test_list_positions() -> None:
    _mod, svc = _svc()
    assert asyncio.run(svc.list())[0]["event_id"] == "e1"


def test_close_publishes_command() -> None:
    positions = load_module("routers.positions")
    pub = FakePublisher()
    svc = positions.PositionsService(pub, FakeReader())
    asyncio.run(svc.close("e1", issued_by="admin"))
    assert pub.calls == [(ControlCommand.CLOSE_POSITION, {"event_id": "e1"}, "admin")]


def test_adjust_publishes_command() -> None:
    positions = load_module("routers.positions")
    pub = FakePublisher()
    svc = positions.PositionsService(pub, FakeReader())
    asyncio.run(svc.adjust("e1", stop_loss=140.0, take_profit=None, issued_by="admin"))
    assert pub.calls == [
        (ControlCommand.ADJUST_SLTP, {"event_id": "e1", "stop_loss": 140.0}, "admin")
    ]
