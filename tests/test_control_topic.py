from cmi_common.events.control import ControlCommandEvent
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_control_topic_registered() -> None:
    assert Topic.CONTROL.value == "control.commands"
    assert TOPIC_EVENT[Topic.CONTROL] is ControlCommandEvent
    assert TOPIC_PARTITIONS[Topic.CONTROL] == 3
