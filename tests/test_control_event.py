from cmi_common.events import parse_event
from cmi_common.events.control import ControlCommand, ControlCommandEvent


def test_control_command_roundtrip() -> None:
    ev = ControlCommandEvent(
        command=ControlCommand.SET_MODE,
        payload={"mode": "demo"},
        issued_by="admin",
    )
    decoded = parse_event(ev.as_kafka_value())
    assert isinstance(decoded, ControlCommandEvent)
    assert decoded.command == ControlCommand.SET_MODE
    assert decoded.payload == {"mode": "demo"}
    assert decoded.issued_by == "admin"


def test_control_command_partition_key_is_stable() -> None:
    ev = ControlCommandEvent(command=ControlCommand.SET_KILL_SWITCH, payload={"enabled": False})
    # All control commands share one partition for global ordering.
    assert ev.partition_key() == "control"
