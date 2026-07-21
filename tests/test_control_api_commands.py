import asyncio

from cmi_common.events.control import ControlCommand
from cmi_common.kafka import Topic
from tests.control_api_helpers import load_module


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append((topic, event))


def test_publish_builds_control_event() -> None:
    commands = load_module("commands")
    producer = FakeProducer()
    pub = commands.CommandPublisher(producer)
    asyncio.run(pub.publish(ControlCommand.SET_MODE, {"mode": "live"}, issued_by="admin"))
    topic, ev = producer.published[0]
    assert topic == Topic.CONTROL
    assert ev.command == ControlCommand.SET_MODE
    assert ev.payload == {"mode": "live"}
    assert ev.issued_by == "admin"
