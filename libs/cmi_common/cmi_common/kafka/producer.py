"""Async Kafka producer wrapper around aiokafka."""

from __future__ import annotations

import logging
from typing import Self

from aiokafka import AIOKafkaProducer

from ..config import KafkaSettings
from ..events.base import BaseEvent
from .topics import Topic

logger = logging.getLogger(__name__)


class EventProducer:
    """Thin, idempotent producer that serializes :class:`BaseEvent` values and
    keys them by the event's ``partition_key`` for per-symbol ordering."""

    def __init__(self, settings: KafkaSettings) -> None:
        self._settings = settings
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.bootstrap_servers,
            client_id=self._settings.client_id,
            enable_idempotence=self._settings.enable_idempotence,
            acks=self._settings.acks,
            compression_type="lz4",
        )
        await self._producer.start()
        logger.info("Kafka producer started (%s)", self._settings.bootstrap_servers)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: Topic, event: BaseEvent) -> None:
        if self._producer is None:
            raise RuntimeError("Producer not started")
        await self._producer.send_and_wait(
            topic.value,
            value=event.as_kafka_value(),
            key=event.partition_key().encode("utf-8"),
        )
        logger.debug("published %s -> %s", event.event_type, topic.value)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()
