"""The two-tier Binance cycle: funding for all, detail for majors."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from service_modules import load_service_module

from cmi_common.kafka import Topic

collector_mod = load_service_module(
    "collector-binance-futures", "application.collector"
)
client_mod = load_service_module(
    "collector-binance-futures", "infrastructure.binance_client"
)


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


class FakeClient:
    near_weight_ceiling = False

    def __init__(self) -> None:
        self.oi_calls: list[str] = []
        self.ls_calls: list[str] = []

    async def premium_index(self) -> list[dict[str, Any]]:
        return [
            {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
            {"symbol": "ETHUSDT", "lastFundingRate": "0.0002"},
            {"symbol": "DOGEUSDT", "lastFundingRate": "0.0003"},
        ]

    async def open_interest(self, ticker: str):
        self.oi_calls.append(ticker)
        return client_mod.OpenInterest(usd=1_000_000.0, change_pct_24h=7.5)

    async def long_short_ratio(self, ticker: str) -> float | None:
        self.ls_calls.append(ticker)
        return 1.8


def _collector(client, producer, majors=frozenset({"BTC"})):
    return collector_mod.BinanceFuturesCollector(
        client,
        producer,
        universe=lambda: ({"BTC", "ETH", "DOGE"}, set(majors), set()),
    )


async def test_every_priced_perp_gets_a_funding_event() -> None:
    producer = FakeProducer()
    await _collector(FakeClient(), producer).poll_once()
    assert {e.symbol for _, e in producer.published} == {"BTC", "ETH", "DOGE"}
    assert all(topic is Topic.DERIVATIVES for topic, _ in producer.published)


async def test_only_majors_incur_the_per_symbol_calls() -> None:
    client = FakeClient()
    await _collector(client, producer=FakeProducer()).poll_once()
    assert client.oi_calls == ["BTCUSDT"]
    assert client.ls_calls == ["BTCUSDT"]


async def test_majors_carry_the_detail_and_others_do_not() -> None:
    producer = FakeProducer()
    await _collector(FakeClient(), producer).poll_once()
    events = {e.symbol: e for _, e in producer.published}
    assert events["BTC"].long_short_account_ratio == 1.8
    assert events["BTC"].open_interest_usd == Decimal("1000000.0")
    assert events["BTC"].open_interest_change_pct_24h == 7.5
    # A non-major is not a degraded reading; those fields were never measured.
    assert events["ETH"].long_short_account_ratio is None
    assert events["ETH"].open_interest_usd is None
    assert events["ETH"].open_interest_change_pct_24h is None
    assert events["ETH"].funding_rate_8h == 0.0002


async def test_a_failing_detail_call_still_publishes_the_funding_reading() -> None:
    class PartialClient(FakeClient):
        async def long_short_ratio(self, ticker: str) -> float | None:
            raise RuntimeError("endpoint flaky")

    producer = FakeProducer()
    await _collector(PartialClient(), producer).poll_once()
    btc = next(e for _, e in producer.published if e.symbol == "BTC")
    assert btc.funding_rate_8h == 0.0001
    assert btc.long_short_account_ratio is None
    # The open interest call is independent and still succeeded.
    assert btc.open_interest_usd == Decimal("1000000.0")


async def test_detail_tier_is_skipped_near_the_weight_ceiling() -> None:
    class HeavyClient(FakeClient):
        near_weight_ceiling = True

    client = HeavyClient()
    producer = FakeProducer()
    await _collector(client, producer).poll_once()
    assert client.oi_calls == []
    # The broad tier already happened, so funding still goes out.
    assert len(producer.published) == 3


async def test_an_empty_broad_tier_is_counted_for_the_outage_warning() -> None:
    # Binance geo-blocks some IP ranges. Renormalisation would absorb a
    # permanently absent positioning axis without a murmur, so the collector
    # has to say so itself.
    class EmptyClient(FakeClient):
        async def premium_index(self) -> list[dict[str, Any]]:
            return []

    collector = _collector(EmptyClient(), FakeProducer())
    await collector.poll_once()
    await collector.poll_once()
    assert collector.empty_cycles == 2


async def test_a_recovered_broad_tier_resets_the_outage_counter() -> None:
    collector = _collector(FakeClient(), FakeProducer())
    collector.empty_cycles = 5
    await collector.poll_once()
    assert collector.empty_cycles == 0


async def test_an_empty_universe_is_reported_rather_than_run_silently() -> None:
    producer = FakeProducer()
    collector = collector_mod.BinanceFuturesCollector(
        FakeClient(), producer, universe=lambda: (set(), set(), set())
    )
    assert await collector.poll_once() == 0
    assert producer.published == []
