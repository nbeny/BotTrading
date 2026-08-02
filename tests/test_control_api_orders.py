# tests/test_control_api_orders.py
import asyncio

from tests.control_api_helpers import load_module
from tests.test_control_api_settings import FakePublisher

from cmi_common.events.control import ControlCommand


def test_place_order_publishes_manual_command() -> None:
    orders = load_module("routers.orders")
    pub = FakePublisher()
    svc = orders.OrdersService(pub)
    asyncio.run(
        svc.place(
            {"symbol": "SOL", "side": "buy", "order_type": "market", "quantity": 1.0},
            issued_by="admin",
        )
    )
    cmd, payload, who = pub.calls[0]
    assert cmd == ControlCommand.MANUAL_ORDER
    assert payload["symbol"] == "SOL" and payload["quantity"] == 1.0
    assert who == "admin"
