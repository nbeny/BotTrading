"""Persistance du journal et complétion par les événements aval."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events.base import Source
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent

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


def _entry(**kw) -> JournalEntryEvent:
    base = {"symbol": "BTC", "signal_event_id": "sig-1", "score": 42,
            "confidence": 0.7, "factors_present": 2}
    base.update(kw)
    return JournalEntryEvent(**base)


def _tables(session) -> list[str]:
    return [
        t.name for t in (getattr(s, "table", None) for s in session.executed)
        if t is not None
    ]


async def test_journal_entry_is_written() -> None:
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(_entry())
    assert s.committed is True
    assert _tables(s) == ["decision_journal"]


async def test_risk_rejection_updates_rather_than_inserts() -> None:
    """Une seconde ligne compterait deux fois la même décision dans chaque
    cohorte de l'analyse ultérieure."""
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC",
                          reason="confidence 0.45 below floor",
                          decision_event_id="dec-1")
    )
    journal_stmts = [
        st for st in s.executed
        if getattr(getattr(st, "table", None), "name", None) == "decision_journal"
    ]
    assert len(journal_stmts) == 1
    assert journal_stmts[0].is_update


async def test_rejection_without_decision_id_writes_no_journal_update() -> None:
    """Sans identifiant de décision il n'y a pas de ligne à rattacher : mieux
    vaut ne rien écrire qu'écrire au hasard."""
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC", reason="x")
    )
    assert "decision_journal" not in _tables(s)


async def test_execution_joins_on_risk_event_id_not_decision_event_id() -> None:
    """ExecutionEvent ne porte pas decision_event_id. Joindre dessus produirait
    un UPDATE qui ne matche jamais — silencieux, et le journal resterait
    éternellement sans résultat d'exécution."""
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(
        ExecutionEvent(kind=ExecutionKind.FILLED, symbol="BTC",
                       risk_event_id="risk-1", fill_price=101.0, pnl=5.0)
    )
    journal_stmts = [
        st for st in s.executed
        if getattr(getattr(st, "table", None), "name", None) == "decision_journal"
    ]
    assert len(journal_stmts) == 1
    assert "risk_event_id" in str(journal_stmts[0])


async def test_journal_entry_is_not_mistaken_for_another_event() -> None:
    """JournalEntryEvent ne doit pas tomber dans une branche voisine du dispatch
    et finir dans la mauvaise table."""
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(_entry(escalated=True, sonnet_called=True, sonnet_validated=True))
    assert _tables(s) == ["decision_journal"]
