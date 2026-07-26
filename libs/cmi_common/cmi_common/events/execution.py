# libs/cmi_common/cmi_common/events/execution.py
"""Execution events produced by the trading-engine after it acts on Kraken."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source
from .decision import Direction


class ExecutionKind(StrEnum):
    SUBMITTED = "submitted"   # order sent to Kraken
    PENDING = "pending"       # queued awaiting operator approval (auto-trading off)
    FILLED = "filled"         # entry filled, SL/TP placed
    CLOSED = "closed"         # position closed (SL/TP hit or manual)
    FAILED = "failed"         # Kraken rejected / error mid-flight
    REJECTED = "rejected"     # blocked by a local guard before any API call


class ExecutionEvent(BaseEvent):
    """Published on ``execution.events`` — the real-world outcome of a signal."""

    event_type: Literal[EventType.EXECUTION] = EventType.EXECUTION
    source: Source = Source.TRADING_ENGINE
    kind: ExecutionKind
    symbol: str
    direction: Direction = Direction.LONG
    # Links back to trades.event_id (the RiskApprovedEvent.event_id).
    risk_event_id: str
    kraken_order_id: str | None = None
    fill_price: float | None = Field(default=None, ge=0)
    size: float | None = Field(default=None, ge=0)
    pnl: float | None = None
    reason: str | None = None

    def partition_key(self) -> str:
        return self.symbol
