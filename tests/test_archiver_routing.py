"""Routage des événements vers l'une des deux tables d'archive."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import (
    AnalysisEvent,
    ControlCommandEvent,
    DecisionEvent,
    DexEvent,
    PriceEvent,
    SentimentEvent,
    VolumeEvent,
)
from cmi_common.events.base import Source
from cmi_common.events.control import ControlCommand
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent

arch = load_service_module("api-gateway", "archiver")


def test_market_events_route_to_the_market_table() -> None:
    """Prix, volume et dex : gros volume, rétention courte."""
    for ev in (
        PriceEvent(
            source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin", price_usd=100.0
        ),
        VolumeEvent(
            source=Source.COINGECKO,
            symbol="BTC",
            coin_id="bitcoin",
            volume_24h_usd=1.0,
            volume_spike_ratio=3.0,
        ),
        DexEvent(
            source=Source.DEXSCREENER,
            symbol="BTC",
            chain="ethereum",
            dex_id="uniswap",
            pair_address="0xpair",
            base_token_address="0xbase",
        ),
    ):
        assert arch.table_for(ev) is arch.MARKET, type(ev).__name__


def test_signal_events_route_to_the_signal_table() -> None:
    """Sentiment, décision, exécution : faible volume, et ce sont ceux qu'on
    relit sans qu'ils existent ailleurs."""
    for ev in (
        SentimentEvent(
            symbol="BTC",
            sentiment_score=0.1,
            confidence=0.5,
            model_name="m",
            input_kind="news",
            sample_size=1,
        ),
        DecisionEvent(symbol="BTC", opportunity_score=1, confidence=0.5, rationale="r"),
        ExecutionEvent(kind=ExecutionKind.FILLED, symbol="BTC", risk_event_id="r1"),
    ):
        assert arch.table_for(ev) is arch.SIGNAL, type(ev).__name__


def test_events_with_a_table_of_their_own_are_not_archived_twice() -> None:
    """Mesuré 8 h après la mise en service : `events_signal` contenait 179467
    RiskRejectedEvent et 179002 AnalysisEvent contre 427 sentiments et 26
    décisions. 99,8 % de doublons, 409 Mo, sur une rétention de 90 jours — soit
    ~110 Go promis sur un VPS où il restait 4,5 Go.

    Les trois ont déjà leur table (`decision_journal`, `signals`,
    `pipeline_rejections`), donc l'archive n'apportait rien qu'une requête ne
    donne déjà."""
    for ev in (
        JournalEntryEvent(
            symbol="BTC",
            signal_event_id="s1",
            score=1,
            confidence=0.5,
            factors_present=1,
        ),
        AnalysisEvent(symbol="BTC", opportunity_score=1, confidence=0.5, reason="r"),
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC", reason="x"),
    ):
        assert arch.table_for(ev) is None, type(ev).__name__


def test_an_unknown_event_lands_in_signal_rather_than_being_dropped() -> None:
    """Un type non prévu doit rester visible. Le jeter ferait disparaître des
    événements sans trace, et la rétention longue est le choix prudent."""

    class Surprise(SentimentEvent):
        pass

    ev = Surprise(
        symbol="BTC",
        sentiment_score=0.0,
        confidence=0.5,
        model_name="m",
        input_kind="news",
        sample_size=1,
    )
    assert arch.table_for(ev) is arch.SIGNAL


def test_row_carries_the_full_payload_and_the_indexed_columns() -> None:
    """Les colonnes indexées sont extraites pour la requête ; le payload garde
    l'événement entier pour que rien ne soit perdu."""
    ev = PriceEvent(
        source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin", price_usd=100.0
    )
    row = arch.to_row(ev, topic="market.price.events")
    assert row["event_id"] == ev.event_id
    assert row["event_type"] == "PriceEvent"
    # `==` seule ne suffit pas : EventType hérite de str, donc le membre d'enum
    # passerait ce test tout en s'affichant « EventType.PRICE » dès qu'un f-string
    # ou une étiquette Prometheus le formate. C'est le type qu'on vérifie.
    assert type(row["event_type"]) is str
    assert row["symbol"] == "BTC"
    assert row["topic"] == "market.price.events"
    assert row["correlation_id"] == ev.correlation_id
    # `price_usd` is a Decimal; Pydantic's JSON mode renders it as a string so
    # the payload stays valid JSONB without losing precision.
    assert float(row["payload"]["price_usd"]) == 100.0
    # Comparaison de valeur, pas seulement de presence du fuseau : un naif
    # n'echoue pas, il decale. Mesure contre la production, un naif ecrit
    # sous TZ=America/New_York s'ecrit avec un offset de quatre heures --
    # une egalite directe avec occurred_at attrape ce decalage la ou
    # `tzinfo is not None` le laisserait passer.
    assert row["time"] == ev.occurred_at


def test_an_event_without_a_symbol_is_archived_with_a_null_symbol() -> None:
    """Tous les événements ne portent pas de symbole ; l'absence ne doit pas
    empêcher l'archivage."""
    ev = ControlCommandEvent(command=ControlCommand.SET_KILL_SWITCH)
    row = arch.to_row(ev, topic="control.commands")
    assert row["symbol"] is None
    assert row["event_id"] == ev.event_id
