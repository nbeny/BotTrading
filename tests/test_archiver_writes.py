"""L'archiveur écrit, sans jamais gêner le reste du système."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import PriceEvent
from cmi_common.events.base import Source
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent

arch = load_service_module("api-gateway", "archiver")


class FakeSession:
    def __init__(self, explode: bool = False) -> None:
        self.executed: list = []
        self.committed = False
        self._explode = explode

    async def execute(self, stmt) -> None:
        if self._explode:
            raise RuntimeError("db down")
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

    def sessionmaker(self):
        return self._session


def _price() -> PriceEvent:
    return PriceEvent(
        source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin", price_usd=100.0
    )


async def test_market_event_is_written_to_the_market_table() -> None:
    s = FakeSession()
    a = arch.EventArchiver(FakeDb(s))
    await a.handle(_price())
    assert s.committed is True
    assert s.executed[0].table.name == "events_market"


async def test_journal_entry_is_skipped_without_touching_the_database() -> None:
    """Pas seulement « non archivé » : aucune session ne doit être ouverte."""
    s = FakeSession()
    a = arch.EventArchiver(FakeDb(s))
    await a.handle(
        JournalEntryEvent(
            symbol="BTC",
            signal_event_id="s1",
            score=1,
            confidence=0.5,
            factors_present=1,
        )
    )
    assert s.executed == []
    assert s.committed is False


async def test_a_write_failure_never_propagates() -> None:
    """L'archive est de l'observabilité. Une panne d'écriture ne doit pas tuer
    le consommateur Kafka partagé et faire perdre des événements métier."""
    s = FakeSession(explode=True)
    a = arch.EventArchiver(FakeDb(s))
    await a.handle(_price())  # ne doit pas lever


async def test_redelivery_is_idempotent() -> None:
    """Kafka est at-least-once : le même événement peut arriver deux fois et ne
    doit pas produire deux lignes."""
    s = FakeSession()
    a = arch.EventArchiver(FakeDb(s))
    ev = _price()
    await a.handle(ev)
    await a.handle(ev)
    assert len(s.executed) == 2
    assert all("ON CONFLICT" in str(st).upper() for st in s.executed)


def test_every_archived_event_records_the_topic_that_carried_it() -> None:
    """`TOPIC_EVENT` ne couvre pas RiskRejectedEvent : les deux moteurs le
    publient sur le topic decision comme piste d'audit. Sans entrée explicite la
    ligne archivée porterait un topic vide et disparaîtrait d'un flux filtré."""
    assert arch._TOPIC_BY_TYPE[PriceEvent] == "market.price.events"
    assert arch._TOPIC_BY_TYPE[RiskRejectedEvent] == "decision.events"
