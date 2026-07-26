"""LexiconLoader: Redis read, in-process caching, seed fallback."""

from __future__ import annotations

from typing import Any

from cmi_common.sources import LEXICON_KEY, LexiconLoader


class FakeCache:
    """Minimal Cache stand-in; counts reads so caching is observable."""

    def __init__(self, value: Any = None, *, raises: bool = False) -> None:
        self.value = value
        self.reads = 0
        self._raises = raises

    async def get_json(self, key: str) -> Any:
        assert key == LEXICON_KEY
        self.reads += 1
        if self._raises:
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
    cache._raises = True
    clock.now = 901.0
    lex = await loader.get()
    assert lex.resolve_ticker("HYPE") == "HYPE"  # not downgraded to seed
