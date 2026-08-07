"""Persister routes derivatives/fundamentals/developer events into their
snapshot hypertables. A partial event persists with NULLs, never zeros."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

from cmi_common.events.base import Source
from cmi_common.events.market import (
    CandleEvent,
    DerivativesEvent,
    DeveloperEvent,
    FundamentalsEvent,
)

persister_mod = load_service_module("api-gateway", "persister")


class FakeSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.committed = False

    async def execute(self, stmt) -> None:
        self.executed.append(stmt)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeDb:
    def __init__(self, session) -> None:
        self._session = session

    def _sessionmaker(self):
        return self._session


async def test_partial_derivatives_event_persists_with_nulls() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0001
        )
    )
    assert session.committed is True
    assert len(session.executed) == 1
    values = session.executed[0].compile().params
    assert values["funding_rate_8h"] == 0.0001
    assert values["open_interest_usd"] is None  # absent ≠ 0
    assert values["venue"] == "binance"


async def test_fundamentals_event_persists() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        FundamentalsEvent(
            source=Source.DEFILLAMA,
            symbol="AAVE",
            coin_id="aave",
            tvl_usd=Decimal("123.45"),
            has_unlock_schedule=True,
        )
    )
    assert session.committed and len(session.executed) == 1
    values = session.executed[0].compile().params
    assert values["coin_id"] == "aave"
    assert values["fees_24h_usd"] is None
    assert values["has_unlock_schedule"] is True


async def test_developer_event_persists() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        DeveloperEvent(
            source=Source.GITHUB,
            symbol="LINK",
            coin_id="chainlink",
            repo_count=3,
            commit_ratio_4w=1.2,
        )
    )
    assert session.committed and len(session.executed) == 1
    values = session.executed[0].compile().params
    assert values["repo_count"] == 3
    assert values["days_since_push"] is None


async def test_candle_event_upserts() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        CandleEvent(
            source=Source.KRAKEN,
            symbol="BTC",
            interval="1h",
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=Decimal("3"),
        )
    )
    assert session.committed and len(session.executed) == 1
    sql = str(session.executed[0])
    # Cible du conflit ET colonnes mises a jour : un ON CONFLICT (time, symbol)
    # sans `interval` fusionnerait silencieusement les series 1h et 15m en une
    # seule ligne, et une colonne oubliee figerait la bougie en formation.
    assert "ON CONFLICT (time, symbol, interval) DO UPDATE" in sql
    for column in ("open", "high", "low", "close", "vwap", "volume", "trades"):
        assert f"{column} = excluded.{column}" in sql
