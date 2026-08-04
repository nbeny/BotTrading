"""La decision est une fonction pure de l'evenement recu.

_market etait le dernier etat du moteur: une lecture de regime tenue en
memoire avec un TTL d'une heure, alimentee par le topic sentiment. Rien ne
l'ecrivait, donc rien ne la rejouait, et le recompute hors-ligne ne pouvait
etre qu'approxime. Portee par les features, elle rend la decision rejouable a
l'identique.
"""

from __future__ import annotations

import inspect
from typing import Any

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

engine_mod = load_service_module("decision-engine", "engine")
main_mod = load_service_module("decision-engine", "main")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


def _analysis(features: dict[str, Any]) -> AnalysisEvent:
    return AnalysisEvent(
        source=Source.AI_HAIKU,
        symbol="BTC",
        opportunity_score=80,
        confidence=0.9,
        reason="test",
        summary="",
        price_change_pct_24h=10.0,
        meta={"features": features},
    )


async def _breakdown(features: dict[str, Any]) -> dict[str, float]:
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis(features))
    return producer.published[0][1].meta["breakdown"]


async def test_market_sentiment_is_read_from_the_event() -> None:
    breakdown = await _breakdown(
        {"price_change_pct_24h": 4.0, "volume_24h_usd": 1e6, "market_sentiment": -0.4}
    )
    assert "news_score" in breakdown


async def test_without_it_the_news_axis_is_excluded() -> None:
    """L'exclusion est le comportement voulu: un axe absent n'est pas note 0,
    il sort du denominateur."""
    breakdown = await _breakdown({"price_change_pct_24h": 4.0, "volume_24h_usd": 1e6})
    assert "news_score" not in breakdown


async def test_an_earlier_event_cannot_change_a_later_score() -> None:
    """La propriete que le rejeu exige."""
    raw = {"price_change_pct_24h": 4.0, "volume_24h_usd": 1e6, "market_sentiment": 0.9}
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis({**raw, "market_sentiment": -0.9}))
    await engine.handle(_analysis(raw))
    assert producer.published[1][1].meta["breakdown"] == await _breakdown(raw)


def test_the_engine_holds_no_market_state() -> None:
    source = inspect.getsource(engine_mod.DecisionEngine)
    assert "_market" not in source
    assert "market_ttl_seconds" not in source


def test_the_engine_no_longer_subscribes_to_sentiment() -> None:
    """La souscription servait uniquement a alimenter l'etat supprime. La
    garder ferait tourner un consommateur qui defile un topic sans rien en
    faire."""
    assert "Topic.SENTIMENT" not in inspect.getsource(main_mod)
