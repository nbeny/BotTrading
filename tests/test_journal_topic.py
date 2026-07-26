"""The journal topic is registered in all three tables that must agree."""

from __future__ import annotations

from cmi_common.events.journal import JournalEntryEvent
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_topic_is_declared() -> None:
    assert Topic.JOURNAL.value == "journal.entries"


def test_event_binding_is_declared() -> None:
    assert TOPIC_EVENT[Topic.JOURNAL] is JournalEntryEvent


def test_partition_count_is_declared() -> None:
    """A topic missing from TOPIC_PARTITIONS is created with the broker default,
    which silently caps consumer-group parallelism."""
    assert TOPIC_PARTITIONS[Topic.JOURNAL] >= 3


def test_every_topic_appears_in_both_tables() -> None:
    """Guards the three-table invariant: adding a Topic member without its
    binding or partition count is a silent misconfiguration, not an error."""
    assert set(TOPIC_EVENT) == set(Topic)
    assert set(TOPIC_PARTITIONS) == set(Topic)
