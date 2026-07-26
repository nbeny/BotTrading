"""La boucle de consommation commite par lot, sans changer ce qui est écrit.

Elle committait l'offset après *chaque* message : un aller-retour vers le broker
par message, en plus de la transaction base. Mesuré en production : ~1330
messages/minute produits pour ~516 consommés, sur une machine dont 24 % du CPU
est déjà repris par l'hyperviseur.

Le lot ne doit rien changer au contenu : mêmes messages, même ordre, même
traitement, un seul commit à la fin.
"""

from __future__ import annotations

import pytest

from cmi_common.events import PriceEvent
from cmi_common.events.base import Source
from cmi_common.kafka.consumer import EventConsumer


class _Msg:
    def __init__(self, value: bytes, offset: int) -> None:
        self.value = value
        self.offset = offset
        self.topic = "market.price.events"


class FakeKafka:
    """Rend les lots un par un, puis arrête la boucle.

    En production un `getmany` vide veut dire « rien pour l'instant » et la
    boucle continue ; ici il faut qu'elle rende la main, d'où le drapeau d'arrêt
    posé par le faux plutôt qu'un paramètre de test dans le code de production.
    """

    def __init__(self, batches: list[list[_Msg]]) -> None:
        self._batches = list(batches)
        self.commits = 0
        self.stopped = None  # posé par _consumer()

    async def getmany(self, timeout_ms=0, max_records=None):
        if not self._batches:
            self.stopped.set()
            return {}
        return {"tp": self._batches.pop(0)}

    async def commit(self) -> None:
        self.commits += 1

    async def stop(self) -> None:
        return None


def _msg(offset: int, symbol: str = "BTC") -> _Msg:
    ev = PriceEvent(
        source=Source.COINGECKO, symbol=symbol, coin_id="bitcoin", price_usd=100.0
    )
    return _Msg(ev.as_kafka_value(), offset)


def _consumer(fake, handler) -> EventConsumer:
    from cmi_common.config import KafkaSettings

    c = EventConsumer(KafkaSettings(), [], handler, group_id="test")
    c._consumer = fake
    fake.stopped = c._stopped
    return c


async def test_every_message_of_a_batch_reaches_the_handler_in_order() -> None:
    """Le lot est un regroupement de commits, pas un échantillonnage."""
    seen: list[str] = []
    fake = FakeKafka([[_msg(i, f"S{i}") for i in range(5)]])
    c = _consumer(fake, lambda e: seen.append(e.symbol))
    await c.run()
    assert seen == ["S0", "S1", "S2", "S3", "S4"]


async def test_one_commit_per_batch_not_per_message() -> None:
    fake = FakeKafka([[_msg(i) for i in range(50)]])
    c = _consumer(fake, lambda e: None)
    await c.run()
    assert fake.commits == 1


async def test_a_failing_message_does_not_drop_the_rest_of_its_batch() -> None:
    """Sans repli, une ligne fautive ferait disparaître les 49 saines — on
    échangerait de la vitesse contre de la perte silencieuse."""
    seen: list[str] = []

    def handler(e):
        if e.symbol == "S2":
            raise RuntimeError("boom")
        seen.append(e.symbol)

    fake = FakeKafka([[_msg(i, f"S{i}") for i in range(5)]])
    c = _consumer(fake, handler)
    await c.run()
    assert seen == ["S0", "S1", "S3", "S4"]
    assert fake.commits == 1


async def test_an_empty_poll_commits_nothing() -> None:
    """Committer à vide serait un aller-retour vers le broker pour rien, à la
    fréquence de la boucle."""
    fake = FakeKafka([])
    c = _consumer(fake, lambda e: None)
    await c.run()
    assert fake.commits == 0


async def test_an_undecodable_message_does_not_stop_the_batch() -> None:
    """Un événement absent de l'union se décode en erreur ; il ne doit pas
    emporter les messages qui le suivent."""
    seen: list[str] = []
    batch = [_msg(0, "S0"), _Msg(b"{ pas du json", 1), _msg(2, "S2")]
    fake = FakeKafka([batch])
    c = _consumer(fake, lambda e: seen.append(e.symbol))
    await c.run()
    assert seen == ["S0", "S2"]


@pytest.mark.parametrize("n", [1, 2, 3])
async def test_successive_batches_each_commit_once(n: int) -> None:
    fake = FakeKafka([[_msg(i)] for i in range(n)])
    c = _consumer(fake, lambda e: None)
    await c.run()
    assert fake.commits == n
