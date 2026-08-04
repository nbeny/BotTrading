"""La lecture de regime voyage avec les features publiees.

Le decision-engine la tenait en memoire, donc rien ne l'ecrivait et rien ne
la rejouait. Elle n'est pas decorative: pour les lignes sans sentiment propre
-- 34,0% mesure sur 276 966 lignes de 24 h -- elle decide si l'axe news_score
(13,8% du poids) est present ou exclu. Elle deplace donc le score *et* le
poids present, ligne par ligne.
"""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

from cmi_common.events import SentimentEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

features_mod = load_service_module("ai-worker-haiku", "features")
worker_mod = load_service_module("ai-worker-haiku", "worker")


class FakeCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_json(self, key: str) -> Any | None:
        return self.values.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.values[key] = value


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


class Clock:
    """Horloge pilotee. Le worker compare `now - last` a SETTLE_S; laisser
    `handle` estampiller avec time.monotonic puis forcer une valeur avant le
    flush rendrait le test dependant de l'uptime de la machine."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _build(cache: FakeCache, clock: Clock | None = None):
    producer = FakeProducer()
    worker = worker_mod.HaikuWorker(
        features_mod.FeatureStore(cache),
        producer,
        regime=features_mod.MarketRegimeStore(cache),
        clock=clock or Clock(),
    )
    return worker, producer


def _sentiment(symbol: str, value: float) -> SentimentEvent:
    return SentimentEvent(
        source=Source.SENTIMENT_SERVICE,
        symbol=symbol,
        sentiment_score=value,
        confidence=0.9,
        model_name="m",
        input_kind="news",
    )


async def _analysis_features(cache: FakeCache) -> dict[str, Any]:
    clock = Clock()
    worker, producer = _build(cache, clock)
    await worker._store.update("BTC", {"price_change_pct_24h": 5.0})
    await worker.handle(_sentiment("BTC", 0.3))
    clock.t = 1_000.0  # la fenetre du symbole est retombee au calme
    await worker.flush_settled()
    return producer.published[0][1].meta["features"]


async def test_a_market_sentiment_event_lands_in_the_regime_store() -> None:
    cache = FakeCache()
    worker, _ = _build(cache)
    await worker.handle(_sentiment("MARKET", -0.4))
    assert await features_mod.MarketRegimeStore(cache).get() == -0.4


async def test_a_per_symbol_sentiment_never_becomes_the_regime() -> None:
    """Sinon la derniere piece analysee deviendrait la « lecture de marche »
    de toutes les autres: une valeur propre a un symbole rebaptisee mesure
    globale, ce que la confiance du modele existe pour empecher -- elle ne
    compte que les preuves specifiques au symbole."""
    cache = FakeCache()
    worker, _ = _build(cache)
    await worker.handle(_sentiment("BTC", 0.8))
    assert await features_mod.MarketRegimeStore(cache).get() is None


async def test_market_never_becomes_a_pending_symbol() -> None:
    """_ready() refuse de scorer un symbole sans prix, donc MARKET n'est jamais
    analyse. L'inscrire au registre des symboles en attente ne ferait que le
    balayer a chaque passage du sweeper."""
    cache = FakeCache()
    worker, _ = _build(cache)
    await worker.handle(_sentiment("MARKET", -0.4))
    assert worker.pending_symbols() == 0


async def test_the_regime_is_stamped_into_the_published_features() -> None:
    cache = FakeCache()
    await features_mod.MarketRegimeStore(cache).set(-0.4)
    assert (await _analysis_features(cache))["market_sentiment"] == -0.4


async def test_an_absent_regime_leaves_the_key_out() -> None:
    """Une cle absente et une cle a None ne doivent pas se confondre en aval:
    le mapping lit `raw.get(...)`, et un 0.0 fabrique ferait passer l'axe
    news_score de exclu a present."""
    assert "market_sentiment" not in await _analysis_features(FakeCache())


async def test_a_measured_neutral_regime_is_stamped() -> None:
    cache = FakeCache()
    await features_mod.MarketRegimeStore(cache).set(0.0)
    assert (await _analysis_features(cache))["market_sentiment"] == 0.0
