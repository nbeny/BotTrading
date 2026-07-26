"""LexiconLoader: Redis read, in-process caching, seed fallback."""

from __future__ import annotations

from typing import Any

from cmi_common.sources import LEXICON_KEY, LexiconLoader


class FakeCache:
    """Minimal Cache stand-in; counts reads so caching is observable."""

    def __init__(self, value: Any = None, *, raises: bool = False) -> None:
        self.value = value
        self.reads = 0
        self.raises = raises

    async def get_json(self, key: str) -> Any:
        assert key == LEXICON_KEY
        self.reads += 1
        if self.raises:
            raise RuntimeError("redis down")
        return self.value


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_builds_lexicon_from_cached_coins() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    loader = LexiconLoader(cache, clock=Clock())
    lex = await loader.get()
    assert lex.resolve_ticker("HYPE") == "HYPE"


async def test_second_call_within_refresh_window_does_not_reread() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=Clock())
    await loader.get()
    await loader.get()
    assert cache.reads == 1


async def test_rereads_after_refresh_window_elapses() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    clock = Clock()
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=clock)
    await loader.get()
    clock.now = 901.0
    await loader.get()
    assert cache.reads == 2


async def test_empty_redis_falls_back_to_seed() -> None:
    loader = LexiconLoader(FakeCache(None), clock=Clock())
    lex = await loader.get()
    assert lex.resolve_ticker("BTC") == "BTC"


async def test_redis_failure_falls_back_to_seed_instead_of_raising() -> None:
    # A Redis blip must degrade recall, never take the collector's loop down.
    loader = LexiconLoader(FakeCache(raises=True), clock=Clock())
    lex = await loader.get()
    assert lex.resolve_ticker("BTC") == "BTC"


async def test_keeps_last_good_lexicon_when_a_later_refresh_fails() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    clock = Clock()
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=clock)
    await loader.get()
    cache.raises = True
    clock.now = 901.0
    lex = await loader.get()
    assert lex.resolve_ticker("HYPE") == "HYPE"  # not downgraded to seed


async def test_seed_is_retried_far_sooner_than_the_full_refresh_window() -> None:
    # On a cold stack the content collectors start alongside collector-coingecko,
    # so lexicon:coins is briefly absent. Waiting the full window there would
    # spend 15 minutes resolving against 50 coins instead of the top 200.
    cache = FakeCache(None)
    clock = Clock()
    loader = LexiconLoader(
        cache, refresh_seconds=900.0, seed_retry_seconds=60.0, clock=clock
    )
    # WIF is outside the bundled seed, so it tells the two states apart.
    assert (await loader.get()).resolve_ticker("WIF") is None

    cache.value = [{"ticker": "WIF", "name": "dogwifhat"}]
    clock.now = 61.0
    assert (await loader.get()).resolve_ticker("WIF") == "WIF"


async def test_seed_is_not_rechecked_on_every_single_call() -> None:
    # A persistently empty Redis must be probed on the retry window, not hit
    # once per call.
    cache = FakeCache(None)
    clock = Clock()
    loader = LexiconLoader(cache, seed_retry_seconds=60.0, clock=clock)
    await loader.get()
    await loader.get()
    await loader.get()
    assert cache.reads == 1


async def test_a_good_lexicon_is_not_rechecked_on_every_call_after_a_failure() -> None:
    cache = FakeCache([{"ticker": "HYPE", "name": "Hyperliquid"}])
    clock = Clock()
    loader = LexiconLoader(cache, refresh_seconds=900.0, clock=clock)
    await loader.get()
    cache.raises = True
    clock.now = 901.0
    await loader.get()  # probes, fails, keeps last good
    await loader.get()  # must not probe again immediately
    assert cache.reads == 2
