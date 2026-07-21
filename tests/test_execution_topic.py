# tests/test_execution_topic.py
from cmi_common.events.execution import ExecutionEvent
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_execution_topic_registered() -> None:
    assert Topic.EXECUTION.value == "execution.events"
    assert TOPIC_EVENT[Topic.EXECUTION] is ExecutionEvent
    assert TOPIC_PARTITIONS[Topic.EXECUTION] == 3
