"""Derivatives and fundamentals reach the per-symbol feature store."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from service_modules import load_service_module

from cmi_common.events import DerivativesEvent, FundamentalsEvent, PriceEvent
from cmi_common.events.base import Source

worker_mod = load_service_module("ai-worker-haiku", "worker")


class FakeStore:
    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}

    async def update(self, symbol: str, fields: dict[str, Any]) -> dict[str, Any]:
        current = self.state.setdefault(symbol, {})
        current.update({k: v for k, v in fields.items() if v is not None})
        return current

    async def get(self, symbol: str) -> dict[str, Any]:
        return self.state.get(symbol, {})


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, topic: Any, event: Any) -> None:
        self.published.append(event)


def _worker(store: FakeStore) -> Any:
    return worker_mod.HaikuWorker(store, FakeProducer())


async def test_derivatives_event_lands_in_the_feature_store() -> None:
    store = FakeStore()
    await _worker(store).handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES,
            symbol="BTC",
            funding_rate_8h=0.0001,
            open_interest_usd=Decimal("1000000"),
            open_interest_change_pct_24h=5.0,
            long_short_account_ratio=1.8,
        )
    )
    features = await store.get("BTC")
    assert features["funding_rate_8h"] == 0.0001
    assert features["open_interest_usd"] == 1000000.0
    assert features["open_interest_change_pct_24h"] == 5.0
    assert features["long_short_account_ratio"] == 1.8


async def test_fundamentals_event_lands_in_the_feature_store() -> None:
    store = FakeStore()
    await _worker(store).handle(
        FundamentalsEvent(
            source=Source.DEFILLAMA,
            symbol="AAVE",
            coin_id="aave",
            tvl_change_pct_7d=3.5,
            fees_change_pct_7d=12.0,
            next_unlock_at=datetime(2026, 8, 10, tzinfo=UTC),
            next_unlock_pct_supply=2.5,
            has_unlock_schedule=True,
        )
    )
    features = await store.get("AAVE")
    assert features["tvl_change_pct_7d"] == 3.5
    assert features["fees_change_pct_7d"] == 12.0
    assert features["next_unlock_pct_supply"] == 2.5
    assert features["has_unlock_schedule"] is True
    # Stored absolute, not as "days remaining": a stored countdown would age
    # silently between the collector's poll and the decision.
    assert features["next_unlock_at"] == "2026-08-10T00:00:00+00:00"


async def test_a_known_empty_schedule_is_stored_as_a_measurement() -> None:
    # has_unlock_schedule=True with no date means "we read it, nothing is
    # coming" -- the best fundamentals reading, and it must survive the store's
    # None-dropping merge. False is not None, so it survives too.
    store = FakeStore()
    await _worker(store).handle(
        FundamentalsEvent(
            source=Source.DEFILLAMA,
            symbol="AAVE",
            coin_id="aave",
            has_unlock_schedule=True,
        )
    )
    features = await store.get("AAVE")
    assert features["has_unlock_schedule"] is True
    assert "next_unlock_at" not in features


async def test_context_events_alone_do_not_make_a_symbol_ready() -> None:
    # Funding is context for a signal, not a signal. Scoring a symbol we have
    # no price for would invent an opportunity out of an exchange statistic.
    store = FakeStore()
    worker = _worker(store)
    await worker.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0001
        )
    )
    assert worker_mod.HaikuWorker._ready(await store.get("BTC")) is False


async def test_a_partial_derivatives_event_does_not_erase_known_fields() -> None:
    # The broad tier republishes funding alone every cycle; if that wiped the
    # majors detail, the positioning axis would oscillate between a rich and a
    # thin reading every few minutes.
    store = FakeStore()
    worker = _worker(store)
    await worker.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES,
            symbol="BTC",
            funding_rate_8h=0.0001,
            long_short_account_ratio=1.8,
        )
    )
    await worker.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0005
        )
    )
    features = await store.get("BTC")
    assert features["funding_rate_8h"] == 0.0005
    assert features["long_short_account_ratio"] == 1.8


async def test_a_measured_zero_funding_survives_the_store() -> None:
    # The store drops None on merge. Zero is not None and must not be dropped:
    # balanced funding is a reading, and an absent axis scores better than a
    # measured one under renormalisation.
    store = FakeStore()
    await _worker(store).handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0
        )
    )
    assert (await store.get("BTC"))["funding_rate_8h"] == 0.0


async def test_price_events_still_work() -> None:
    store = FakeStore()
    await _worker(store).handle(
        PriceEvent(
            source=Source.COINGECKO,
            symbol="BTC",
            coin_id="bitcoin",
            price_usd=Decimal("60000"),
            price_change_pct_24h=5.0,
        )
    )
    assert (await store.get("BTC"))["price_change_pct_24h"] == 5.0
