"""Persister routes RiskRejectedEvent into pipeline_rejections, stage-tagged.

Both the decision engine and the risk engine publish rejections on the decision
topic, which the api-gateway already consumed and silently discarded. Without
these rows the funnel can count how many signals vanished but not why.
"""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events.base import Source
from cmi_common.events.risk import RiskRejectedEvent

persister_mod = load_service_module("api-gateway", "persister")


def test_stage_from_source_maps_both_producers() -> None:
    assert persister_mod.stage_for(Source.DECISION_ENGINE) == "decision_engine"
    assert persister_mod.stage_for(Source.RISK_ENGINE) == "risk_engine"


def test_source_arrives_as_a_plain_string_on_a_real_event() -> None:
    """BaseEvent sets use_enum_values=True, so `source` is a str, not a Source
    member. The dict lookup still resolves because Source is a str enum, but any
    code reaching for `.value` would raise inside the Kafka consumer loop."""
    ev = RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC", reason="x")
    assert isinstance(ev.source, str)
    assert not isinstance(ev.source, Source)
    assert persister_mod.stage_for(ev.source) == "risk_engine"


def test_unknown_source_keeps_its_raw_name() -> None:
    """An unmapped producer must stay visible in the funnel rather than be
    dropped — and must not crash the consumer on the way."""
    assert persister_mod.stage_for("some-future-service") == "some-future-service"


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


async def test_rejection_event_is_persisted() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        RiskRejectedEvent(
            source=Source.RISK_ENGINE, symbol="BTC", reason="score 22 below floor"
        )
    )
    assert session.committed is True
    assert len(session.executed) == 1


async def test_rejection_is_not_mistaken_for_a_decision() -> None:
    """RiskRejectedEvent and DecisionEvent share the decision topic. If the
    dispatch order were wrong, a rejection would be written to the decisions
    table and counted as an approval — inverting the funnel's meaning."""
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC", reason="x")
    )
    stmt = session.executed[0]
    assert stmt.table.name == "pipeline_rejections"
