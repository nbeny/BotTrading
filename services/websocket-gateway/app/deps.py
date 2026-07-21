"""Dependency container for the websocket-gateway.

Groups the long-lived singletons (the connection manager and the Kafka
broadcast consumer) so ``main`` can build, store and tear them down as a unit,
and so request/WebSocket handlers can fetch them off ``app.state`` via a single
typed accessor rather than reaching into loose attributes.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket

from cmi_common.config import Settings

from .consumer import BroadcastConsumer
from .manager import ConnectionManager


@dataclass
class Deps:
    """Service-wide singletons stored on ``app.state.deps``."""

    manager: ConnectionManager
    consumer: BroadcastConsumer

    @classmethod
    def build(cls, settings: Settings) -> Deps:
        manager = ConnectionManager()
        consumer = BroadcastConsumer(
            settings.kafka, manager, group_id="websocket-gateway"
        )
        return cls(manager=manager, consumer=consumer)


def get_deps(websocket: WebSocket) -> Deps:
    """Fetch the dependency container off the running app's state."""
    deps: Deps = websocket.app.state.deps
    return deps
