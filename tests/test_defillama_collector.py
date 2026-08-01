"""One DefiLlama polling cycle, including the unlock round-robin."""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

from cmi_common.kafka import Topic

collector_mod = load_service_module("collector-defillama", "application.collector")
unlocks = load_service_module("collector-defillama", "domain.unlocks")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


class FakeClient:
    def __init__(self, slugs: set[str] | None = None) -> None:
        self.unlock_calls: list[str] = []
        self._slugs = slugs if slugs is not None else {"aave"}

    async def protocols(self) -> list[dict[str, Any]]:
        # Shaped like the live payload: deployment slugs under a parent, which
        # is what the emissions list is actually keyed by.
        return [
            {
                "slug": "aave-v3",
                "parentProtocol": "parent#aave",
                "gecko_id": "aave",
                "tvl": 3_000_000.0,
                "change_7d": 3.5,
            },
            {
                "slug": "aave-v2",
                "parentProtocol": "parent#aave",
                "gecko_id": "aave",
                "tvl": 2_000_000.0,
                "change_7d": 3.5,
            },
            {"slug": "uniswap", "gecko_id": "uniswap", "tvl": 4.0, "change_7d": 1.0},
        ]

    async def fees(self) -> dict[str, dict[str, Any]]:
        return {}

    async def emission_slugs(self) -> set[str]:
        return self._slugs

    async def cached_unlock(self, coin_id: str):
        # Nothing cached by default, so the existing tests keep exercising the
        # fetch path and its budget.
        return False, None

    async def unlock(self, slug: str, coin_id: str):
        self.unlock_calls.append(slug)
        return None


KNOWN = {"aave": "AAVE", "uniswap": "UNI"}


def _collector(client, producer, **kw):
    return collector_mod.DefiLlamaCollector(
        client, producer, known_tokens=lambda: KNOWN, **kw
    )


async def test_cycle_publishes_one_event_per_tracked_token() -> None:
    producer = FakeProducer()
    await _collector(FakeClient(), producer).poll_once()
    assert {e.symbol for _, e in producer.published} == {"AAVE", "UNI"}
    assert all(topic is Topic.FUNDAMENTALS for topic, _ in producer.published)


async def test_only_protocols_with_a_schedule_are_ever_fetched() -> None:
    # Fetching a 2.25 MB document for a token with no schedule is pure waste.
    client = FakeClient(slugs={"aave"})
    await _collector(client, FakeProducer()).poll_once()
    assert client.unlock_calls == ["aave"]


async def test_the_parent_slug_is_what_the_emissions_list_is_matched_on() -> None:
    # Matching on the deployment slug finds nothing for "aave" and the token
    # silently reports no unlock schedule at all. Measured on live data,
    # slug-matching loses 139 of 359 protocols.
    client = FakeClient(slugs={"aave"})
    producer = FakeProducer()
    await _collector(client, producer).poll_once()
    aave = next(e for _, e in producer.published if e.symbol == "AAVE")
    assert aave.has_unlock_schedule is True


async def test_deployments_sharing_a_parent_are_fetched_once() -> None:
    # Aave is seven rows on the live API. One 2.25 MB document per deployment
    # would multiply the cost by the deployment count for no new information.
    client = FakeClient(slugs={"aave"})
    await _collector(client, FakeProducer()).poll_once()
    assert client.unlock_calls == ["aave"]


async def test_the_round_robin_caps_fetches_per_cycle_and_advances() -> None:
    client = FakeClient(slugs={"aave", "uniswap"})
    collector = _collector(client, FakeProducer(), max_unlock_fetches=1)
    await collector.poll_once()
    await collector.poll_once()
    # One per cycle, and the second cycle moves on rather than re-fetching.
    assert len(client.unlock_calls) == 2
    assert set(client.unlock_calls) == {"aave", "uniswap"}


async def test_a_token_whose_unlock_fetch_failed_reports_no_schedule() -> None:
    # Absent, not "nothing is coming". A key inserted here would become
    # has_unlock_schedule=True and earn a perfect fundamentals score for a
    # token whose dilution we simply failed to read.
    class FailingClient(FakeClient):
        async def unlock(self, slug: str, coin_id: str):
            raise RuntimeError("CDN down")

    producer = FakeProducer()
    await _collector(FailingClient(), producer).poll_once()
    aave = next(e for _, e in producer.published if e.symbol == "AAVE")
    assert aave.has_unlock_schedule is False


async def test_an_unsizable_schedule_also_leaves_the_key_absent() -> None:
    # next_unlock raises ValueError when an unlock is scheduled but cannot be
    # sized. That must reach this layer and leave the token unknown.
    class RaisingClient(FakeClient):
        async def unlock(self, slug: str, coin_id: str):
            raise ValueError("unlock scheduled but maxSupply is missing or zero")

    producer = FakeProducer()
    await _collector(RaisingClient(), producer).poll_once()
    aave = next(e for _, e in producer.published if e.symbol == "AAVE")
    assert aave.has_unlock_schedule is False


async def test_a_successful_lookup_with_no_pending_unlock_reports_a_schedule() -> None:
    # The other side of the distinction: we read the schedule and it is empty.
    class CleanClient(FakeClient):
        async def unlock(self, slug: str, coin_id: str):
            self.unlock_calls.append(slug)
            return None

    producer = FakeProducer()
    await _collector(CleanClient(), producer).poll_once()
    aave = next(e for _, e in producer.published if e.symbol == "AAVE")
    assert aave.has_unlock_schedule is True
    assert aave.next_unlock_at is None


async def test_cached_schedules_are_reported_for_every_eligible_token() -> None:
    # The budget bounds 2.25 MB fetches, not map membership. A token whose
    # schedule is already in Redis must carry has_unlock_schedule=True on every
    # cycle -- reporting "untracked" instead drops the dilution axis, and
    # renormalisation then scores that token *higher* than one we did read.
    # Measured before the fix on a 40-protocol universe with everything cached:
    # 3 tokens reported a schedule, 37 reported unknown, every cycle.
    class CachedClient(FakeClient):
        async def cached_unlock(self, coin_id: str):
            return True, None

        async def unlock(self, slug: str, coin_id: str):
            raise AssertionError("must not fetch what is already cached")

    producer = FakeProducer()
    await _collector(
        CachedClient(slugs={"aave", "uniswap"}), producer, max_unlock_fetches=1
    ).poll_once()
    assert {e.symbol for _, e in producer.published} == {"AAVE", "UNI"}
    assert all(e.has_unlock_schedule for _, e in producer.published)


async def test_the_fetch_budget_still_bounds_uncached_lookups() -> None:
    # The other side: a cache miss is what the budget is for.
    client = FakeClient(slugs={"aave", "uniswap"})
    await _collector(client, FakeProducer(), max_unlock_fetches=1).poll_once()
    assert len(client.unlock_calls) == 1


async def test_an_empty_token_universe_is_reported_rather_than_run_silently() -> None:
    # Otherwise a cold start or a persister outage looks byte-identical to
    # "no DefiLlama protocol is in our universe", after paying for the 3.7 MB
    # fees payload.
    producer = FakeProducer()
    collector = collector_mod.DefiLlamaCollector(
        FakeClient(), producer, known_tokens=lambda: {}
    )
    assert await collector.poll_once() == 0
    assert producer.published == []
