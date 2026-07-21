"""Control commands issued by control-api and applied by the trading-engine."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class ControlCommand(str, Enum):
    SET_MODE = "set_mode"
    SET_KILL_SWITCH = "set_kill_switch"
    SET_AUTO_TRADING = "set_auto_trading"
    SET_CAPS = "set_caps"
    CLOSE_POSITION = "close_position"
    ADJUST_SLTP = "adjust_sltp"
    MANUAL_ORDER = "manual_order"
    APPROVE_OPPORTUNITY = "approve_opportunity"
    REJECT_OPPORTUNITY = "reject_opportunity"


class ControlCommandEvent(BaseEvent):
    """Published on ``control.commands`` — an operator intent for the engine."""

    event_type: Literal[EventType.CONTROL_COMMAND] = EventType.CONTROL_COMMAND
    source: Source = Source.CONTROL_API
    command: ControlCommand
    payload: dict[str, Any] = Field(default_factory=dict)
    issued_by: str | None = None

    def partition_key(self) -> str:
        # Single partition so commands apply in a total order.
        return "control"
