"""Le snapshot de compte est un citoyen de première classe du bus.

Un événement absent de l'union `AnyEvent` se publie parfaitement et échoue à la
*consommation* — c'est exactement ce qui est arrivé à JournalEntryEvent. Le
round-trip est donc le test qui compte, pas la simple construction.
"""

from __future__ import annotations

from cmi_common.events import AccountSnapshotEvent, parse_event
from cmi_common.events.base import EventType
from cmi_common.kafka import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def _snapshot() -> AccountSnapshotEvent:
    return AccountSnapshotEvent(
        venue="kraken_spot",
        equity_usd=1234.56,
        cash_usd=1000.0,
        balances={"ZUSD": 1000.0, "XXBT": 0.01},
    )


def test_round_trips_through_the_discriminated_union() -> None:
    decoded = parse_event(_snapshot().as_kafka_value())
    assert isinstance(decoded, AccountSnapshotEvent)
    assert decoded.venue == "kraken_spot"
    assert decoded.equity_usd == 1234.56


def test_event_type_is_the_plain_class_name() -> None:
    """`use_enum_values=True` ne s'applique pas aux valeurs par défaut, donc
    l'attribut reste un membre d'enum ; c'est sa *valeur* qui doit être le nom
    de classe, sans quoi tout formatage rendrait « EventType.ACCOUNT_SNAPSHOT »."""
    assert EventType.ACCOUNT_SNAPSHOT.value == "AccountSnapshotEvent"


def test_the_topic_appears_in_all_three_tables() -> None:
    assert Topic.ACCOUNT_SNAPSHOT in TOPIC_EVENT
    assert Topic.ACCOUNT_SNAPSHOT in TOPIC_PARTITIONS
    assert TOPIC_EVENT[Topic.ACCOUNT_SNAPSHOT] is AccountSnapshotEvent


def test_partition_key_is_the_venue() -> None:
    """Les snapshots d'un même venue doivent rester ordonnés entre eux : un
    snapshot périmé livré après un frais afficherait un solde qui recule."""
    assert _snapshot().partition_key() == "kraken_spot"
