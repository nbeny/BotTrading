import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, command, payload, *, issued_by):
        self.calls.append((command, payload, issued_by))


class FakeReader:
    async def settings(self):
        return {
            "mode": "dry_run",
            "trading_enabled": True,
            "auto_trading_enabled": True,
        }


def _svc():
    return load_module("routers.settings")


def test_set_mode_publishes_command() -> None:
    settings = _svc()
    pub = FakePublisher()
    svc = settings.SettingsService(pub, FakeReader())
    asyncio.run(svc.set_mode("demo", issued_by="admin"))
    assert pub.calls == [(ControlCommand.SET_MODE, {"mode": "demo"}, "admin")]


def test_set_mode_rejects_invalid() -> None:
    settings = _svc()
    svc = settings.SettingsService(FakePublisher(), FakeReader())
    import pytest

    with pytest.raises(ValueError):
        asyncio.run(svc.set_mode("banana", issued_by="admin"))


def test_set_caps_publishes_only_given_fields() -> None:
    settings = _svc()
    pub = FakePublisher()
    svc = settings.SettingsService(pub, FakeReader())
    asyncio.run(svc.set_caps({"max_order_usd": 250.0}, issued_by="admin"))
    assert pub.calls == [(ControlCommand.SET_CAPS, {"max_order_usd": 250.0}, "admin")]


def test_status_reads_reader() -> None:
    settings = _svc()
    svc = settings.SettingsService(FakePublisher(), FakeReader())
    status = asyncio.run(svc.status())
    assert status["mode"] == "dry_run"
