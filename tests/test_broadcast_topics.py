"""BROADCAST_TOPICS must carry every market topic the terminal subscribes to.

The list is an explicit subscription, not a wildcard — a topic missing here is
a family of events that silently never reaches the front."""

from service_modules import load_service_module

from cmi_common.kafka import Topic

consumer_mod = load_service_module("websocket-gateway", "consumer")


def test_derived_market_topics_are_broadcast() -> None:
    topics = set(consumer_mod.BROADCAST_TOPICS)
    assert Topic.DERIVATIVES in topics
    assert Topic.FUNDAMENTALS in topics
    assert Topic.DEVELOPER in topics
