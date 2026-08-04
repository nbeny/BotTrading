"""La lecture de regime a sa propre cle et son propre TTL.

Le decision-engine la tenait en memoire avec un TTL de 3600 s. La deplacer
dans le FeatureStore telle quelle l'aurait ramenee a 900 s, soit une fenetre
quatre fois plus courte que celle que la production applique aujourd'hui.
"""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

features_mod = load_service_module("ai-worker-haiku", "features")
MarketRegimeStore = features_mod.MarketRegimeStore


class FakeCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def get_json(self, key: str) -> Any | None:
        return self.values.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_seconds


async def test_absent_regime_reads_as_none() -> None:
    assert await MarketRegimeStore(FakeCache()).get() is None


async def test_a_stored_regime_reads_back() -> None:
    store = MarketRegimeStore(FakeCache())
    await store.set(-0.42)
    assert await store.get() == -0.42


async def test_a_measured_zero_is_not_an_absence() -> None:
    """0.0 est un regime neutre mesure. Le confondre avec l'absence ferait
    disparaitre l'axe news_score pour les symboles sans sentiment propre --
    la conflation None/0 que ce projet paie a chaque etage."""
    store = MarketRegimeStore(FakeCache())
    await store.set(0.0)
    assert await store.get() == 0.0


async def test_the_ttl_matches_the_engine_window_it_replaces() -> None:
    cache = FakeCache()
    await MarketRegimeStore(cache).set(0.1)
    assert cache.ttls[features_mod.REGIME_KEY] == 3600
    assert features_mod.REGIME_TTL == 3600


async def test_the_regime_key_is_not_a_symbol_feature_key() -> None:
    """Sinon elle heriterait du TTL de 900 s du FeatureStore."""
    assert features_mod.KEY.format(symbol="MARKET") != features_mod.REGIME_KEY
