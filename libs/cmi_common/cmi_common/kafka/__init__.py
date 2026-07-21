"""Kafka helpers: typed producer/consumer and topic registry."""

from .consumer import EventConsumer, Handler
from .producer import EventProducer
from .topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic

__all__ = [
    "EventConsumer",
    "EventProducer",
    "Handler",
    "TOPIC_EVENT",
    "TOPIC_PARTITIONS",
    "Topic",
]
