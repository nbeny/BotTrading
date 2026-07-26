"""Async Kafka consumer wrapper around aiokafka.

Provides an at-least-once consume loop with manual commit after successful
handling, plus typed decoding into concrete event models.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from aiokafka import AIOKafkaConsumer

from ..config import KafkaSettings
from ..events import BaseEvent, parse_event
from .topics import Topic

logger = logging.getLogger(__name__)

Handler = Callable[[BaseEvent], Awaitable[None]]


class EventConsumer:
    """Consumes one or more topics and dispatches decoded events to a handler.

    Uses manual offset commits: the offset is only committed once the handler
    returns successfully, giving at-least-once delivery. Handlers must be
    idempotent (see ``correlation_id`` / ``event_id`` for dedup keys).
    """

    def __init__(
        self,
        settings: KafkaSettings,
        topics: Sequence[Topic],
        handler: Handler,
        *,
        group_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._topics = topics
        self._handler = handler
        self._group_id = group_id or settings.group_id
        self._consumer: AIOKafkaConsumer | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *[t.value for t in self._topics],
            bootstrap_servers=self._settings.bootstrap_servers,
            group_id=self._group_id,
            client_id=self._settings.client_id,
            auto_offset_reset=self._settings.auto_offset_reset,
            enable_auto_commit=False,
            max_poll_records=self._settings.max_poll_records,
            max_poll_interval_ms=self._settings.max_poll_interval_ms,
        )
        await self._consumer.start()
        logger.info(
            "Kafka consumer started group=%s topics=%s",
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
                    event = parse_event(msg.value)
                    await self._handler(event)
                    await self._consumer.commit()
                except Exception:
                    logger.exception(
                        "handler failed topic=%s offset=%s", msg.topic, msg.offset
                    )
                    # Offset not committed -> message will be retried. A real
                    # deployment would route poison messages to a DLQ here.
        finally:
            await self.stop()
