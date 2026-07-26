"""Le worker Sonnet journalise chaque analyse, y compris celles qu'il ignore."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent
from cmi_common.kafka import Topic

sw = load_service_module("ai-worker-sonnet", "worker")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic, event) -> None:
        self.published.append((topic, event))


class BlockingCache:
    """Refuse toujours l'appel : simule cooldown ou budget épuisé."""

    async def get_json(self, key):
        return 1

    async def set_json(self, key, value, ttl_seconds=0):
        return None

    async def allow(self, key, limit, window):
        return False


class PermissiveCache:
    async def get_json(self, key):
        return None

    async def set_json(self, key, value, ttl_seconds=0):
        return None

    async def allow(self, key, limit, window):
        return True


def _analysis(**kw) -> AnalysisEvent:
    base = {
        "symbol": "BTC",
        "opportunity_score": 42,
        "confidence": 0.7,
        "reason": "r",
        "meta": {"factors": {"momentum": 0.8}, "features": {}},
    }
    base.update(kw)
    return AnalysisEvent(**base)


def _journals(producer) -> list:
    return [e for t, e in producer.published if t is Topic.JOURNAL]


async def test_non_escalated_analysis_is_journalled() -> None:
    """Le groupe témoin : sans ces lignes, le gate reste invérifiable."""
    p = FakeProducer()
    w = sw.SonnetWorker(claude=None, producer=p, cache=BlockingCache())
    await w.handle(_analysis(escalate=False))
    entries = _journals(p)
    assert len(entries) == 1
    assert entries[0].escalated is False
    assert entries[0].sonnet_called is False


async def test_suppressed_call_is_journalled_with_its_reason() -> None:
    """Un appel supprimé disparaissait dans les logs. C'est exactement ce que le
    journal doit capter : 521 escalades pour 11 appels réels."""
    p = FakeProducer()
    w = sw.SonnetWorker(claude=None, producer=p, cache=BlockingCache())
    await w.handle(_analysis(escalate=True))
    entries = _journals(p)
    assert len(entries) == 1
    assert entries[0].escalated is True
    assert entries[0].sonnet_called is False
    assert entries[0].skip_reason == "cooldown_or_budget"


async def test_a_non_analysis_event_produces_no_journal() -> None:
    """Le worker consomme un topic typé ; un événement d'un autre type ne doit
    produire ni analyse ni ligne de journal."""
    from cmi_common.events import PriceEvent
    from cmi_common.events.base import Source

    p = FakeProducer()
    w = sw.SonnetWorker(claude=None, producer=p, cache=BlockingCache())
    await w.handle(
        PriceEvent(
            source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin", price_usd=100.0
        )
    )
    assert _journals(p) == []


async def test_journal_failure_does_not_break_the_worker() -> None:
    """Perdre une ligne de journal est acceptable ; interrompre le pipeline de
    trading ne l'est pas."""

    class Exploding(FakeProducer):
        async def publish(self, topic, event):
            if topic is Topic.JOURNAL:
                raise RuntimeError("kafka down")
            await super().publish(topic, event)

    p = Exploding()
    w = sw.SonnetWorker(claude=None, producer=p, cache=BlockingCache())
    await w.handle(_analysis(escalate=False))  # ne doit pas lever


async def test_journal_failure_does_not_suppress_the_decision() -> None:
    """Cas le plus grave : si la publication du journal échoue APRÈS qu'une
    décision a été produite, la décision doit quand même partir. Le journal est
    de l'observabilité, pas un maillon du pipeline."""

    class ExplodingJournal(FakeProducer):
        async def publish(self, topic, event):
            if topic is Topic.JOURNAL:
                raise RuntimeError("kafka down")
            await super().publish(topic, event)

    class FakeClaude:
        async def complete(self, system, prompt, service):
            class R:
                @staticmethod
                def json():
                    return {"validated": True, "direction": "long",
                            "opportunity_score": 71, "confidence": 0.8,
                            "rationale": "ok", "key_risks": []}
            return R()

    p = ExplodingJournal()
    w = sw.SonnetWorker(claude=FakeClaude(), producer=p, cache=PermissiveCache())
    await w.handle(_analysis(escalate=True))
    decisions = [e for t, e in p.published if t is Topic.DECISION]
    assert len(decisions) == 1
