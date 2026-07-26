"""Periodic read-only snapshot of an exchange account.

Published by trading-engine, which is the only service holding exchange
credentials. The api-gateway persists it and the read plane serves it, so the
publicly exposed service never needs a key of its own.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class AccountSnapshotEvent(BaseEvent):
    """One venue's balance at one instant.

    `equity_usd` is the whole account valued in USD; `cash_usd` is the quote
    currency alone. They differ as soon as anything is held in coin, and the
    portfolio page needs both.
    """

    event_type: Literal[EventType.ACCOUNT_SNAPSHOT] = EventType.ACCOUNT_SNAPSHOT
    source: Source = Source.TRADING_ENGINE

    venue: str
    equity_usd: float
    cash_usd: float
    balances: dict[str, float] = Field(default_factory=dict)

    def partition_key(self) -> str:
        # Keyed by venue so a venue's snapshots stay ordered against each other:
        # a stale snapshot delivered after a fresh one would show the balance
        # going backwards.
        return self.venue
