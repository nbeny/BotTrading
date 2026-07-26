"""Le snapshot de compte atterrit en base une fois et une seule."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events.account import AccountSnapshotEvent

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

    def sessionmaker(self):
        return self._session


def _snapshot() -> AccountSnapshotEvent:
    return AccountSnapshotEvent(
        venue="kraken-spot",
        equity_usd=1234.5,
        cash_usd=1000.0,
        balances={"ZUSD": 1000.0, "XXBT": 0.004},
    )


async def test_snapshot_is_written_to_account_snapshots() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(_snapshot())
    assert session.committed is True
    assert len(session.executed) == 1
    assert session.executed[0].table.name == "account_snapshots"


async def test_redelivery_is_idempotent() -> None:
    """Kafka est at-least-once : un message redélivré porte un événement
    identique et ne doit pas produire une seconde ligne."""
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    event = _snapshot()
    await p.handle(event)
    await p.handle(event)
    assert len(session.executed) == 2
    assert all("ON CONFLICT" in str(st).upper() for st in session.executed)
