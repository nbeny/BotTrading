"""Kafka -> WebSocket broadcast consumer.

Unlike :class:`cmi_common.kafka.EventConsumer` (which hands the handler a decoded
event but drops the topic), the gateway needs the *raw* Kafka topic name so it
can put it in the outgoing frame. So this thin wrapper drives ``aiokafka``
directly, decoding each value as JSON and building the frame

    {"topic": "<kafka topic>", "event": <event dict>, "ts": "<iso8601>"}

before calling :meth:`ConnectionManager.broadcast`. It mirrors the lifecycle of
``EventConsumer`` (``start`` / ``run`` / ``stop``) so ``main`` wires it the same
way as every other service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from aiokafka import AIOKafkaConsumer

from cmi_common.config import KafkaSettings
from cmi_common.kafka import Topic

from .manager import ConnectionManager

logger = logging.getLogger(__name__)

BROADCAST_TOPICS: tuple[Topic, ...] = (
    Topic.PRICE,
    Topic.VOLUME,
    Topic.DEX,
    Topic.NEWS,
    Topic.SOCIAL,
    Topic.SENTIMENT,
    Topic.ANALYSIS,
    Topic.DECISION,
    Topic.RISK_APPROVED,
    Topic.EXECUTION,
    Topic.ACCOUNT_SNAPSHOT,
    Topic.DERIVATIVES,
    Topic.FUNDAMENTALS,
    Topic.DEVELOPER,
    Topic.CANDLES,
)


class BroadcastConsumer:
    """Consumes market/analysis/decision/risk topics and fans them out to WS."""

    def __init__(
        self,
        settings: KafkaSettings,
        manager: ConnectionManager,
        *,
        topics: Sequence[Topic] = BROADCAST_TOPICS,
        group_id: str = "websocket-gateway",
    ) -> None:
        self._settings = settings
        self._manager = manager
        self._topics = tuple(topics)
        self._group_id = group_id
        self._consumer: AIOKafkaConsumer | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *[t.value for t in self._topics],
            bootstrap_servers=self._settings.bootstrap_servers,
            group_id=self._group_id,
            client_id=self._settings.client_id,
            # Terminal clients only care about live events; skip the backlog.
            auto_offset_reset="latest",
            enable_auto_commit=True,
            max_poll_records=self._settings.max_poll_records,
        )
        await self._consumer.start()
        logger.info(
            "broadcast consumer started group=%s topics=%s",
            self._group_id,
            [t.value for t in self._topics],
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def run(self) -> None:
        """Main consume loop. Runs until :meth:`stop` is called."""
        if self._consumer is None:
            raise RuntimeError("Consumer not started")
        try:
            async for msg in self._consumer:
                if self._stopped.is_set():
                    break
                try:
                    frame = self._build_frame(msg.topic, msg.value)
                    await self._manager.broadcast(frame)
                except Exception:  # never kill the loop
                    logger.exception(
                        "broadcast failed topic=%s offset=%s", msg.topic, msg.offset
                    )
        finally:
            await self.stop()

    @staticmethod
    def _build_frame(topic: str, value: bytes | None) -> dict[str, Any]:
        event: Any = None
        if value is not None:
            event = json.loads(value)
        return {
            "topic": topic,
            "event": event,
            "ts": datetime.now(tz=UTC).isoformat(),
        }
