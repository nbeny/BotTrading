"""Publishes ControlCommandEvent to control.commands. The only writer path."""
from __future__ import annotations

from typing import Any

from cmi_common.events.control import ControlCommand, ControlCommandEvent
from cmi_common.kafka import Topic


class CommandPublisher:
    def __init__(self, producer) -> None:
        self._producer = producer

    async def publish(
        self, command: ControlCommand, payload: dict[str, Any], *, issued_by: str | None
    ) -> ControlCommandEvent:
        event = ControlCommandEvent(command=command, payload=payload, issued_by=issued_by)
        await self._producer.publish(Topic.CONTROL, event)
        return event
