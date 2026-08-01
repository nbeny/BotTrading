"""Une inférence par symbole et par cycle de collecte — pas un filtre d'événements.

Mesuré en production : 200 prix/min entrants produisaient 376 analyses/min, parce
que `handle` émettait à chaque événement reçu. Un symbole recevant son
`PriceEvent` puis son `VolumeEvent` du même sondage à quelques secondes d'écart
produisait deux analyses, la première étant une vue partielle de la seconde.

La règle vérifiée ici : **rien n'est ignoré**. Chaque événement met à jour l'état
des features ; seule la publication de l'objet *dérivé* est agrégée, et ce qui
est publié est calculé sur l'état le plus complet de la fenêtre.
"""

from __future__ import annotations

from service_modules import load_service_module

worker_mod = load_service_module("ai-worker-haiku", "worker")


class FakeStore:
    """Reproduit la fusion monotone du vrai FeatureStore."""

    def __init__(self) -> None:
        self.state: dict[str, dict] = {}
        self.updates = 0

    async def update(self, symbol: str, fields: dict) -> dict:
        self.updates += 1
        cur = self.state.setdefault(symbol, {})
        cur.update({k: v for k, v in fields.items() if v is not None})
        return dict(cur)

    async def get(self, symbol: str) -> dict:
        return dict(self.state.get(symbol, {}))


class FakeProducer:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, topic, event) -> None:
        self.published.append(event)


def _price(symbol="BTC", chg=5.0):
    from cmi_common.events import PriceEvent
    from cmi_common.events.base import Source

    return PriceEvent(
        source=Source.COINGECKO,
        symbol=symbol,
        coin_id="bitcoin",
        price_usd=100.0,
        price_change_pct_24h=chg,
        volume_24h_usd=2e10,
    )


def _volume(symbol="BTC", ratio=3.0):
    from cmi_common.events import VolumeEvent
    from cmi_common.events.base import Source

    return VolumeEvent(
        source=Source.COINGECKO,
        symbol=symbol,
        coin_id="bitcoin",
        volume_24h_usd=2e10,
        volume_spike_ratio=ratio,
    )


def _sentiment(symbol="BTC", score=0.4):
    from cmi_common.events import SentimentEvent

    return SentimentEvent(
        symbol=symbol,
        sentiment_score=score,
        confidence=0.8,
        model_name="m",
        input_kind="news",
        sample_size=10,
    )


def _worker(store, producer, clock):
    return worker_mod.HaikuWorker(store, producer, clock=clock)


class Clock:
    """Horloge pilotée : le comportement testé est temporel, le laisser au vrai
    temps rendrait le test lent et instable."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── ce qui n'est jamais perdu ────────────────────────────────────────────────
async def test_every_event_updates_the_features_even_when_no_analysis_is_emitted() -> (
    None
):
    """Le point central : agréger l'inférence ne doit pas filtrer les entrées."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price(chg=5.0))
    await w.handle(_volume(ratio=3.0))
    await w.handle(_sentiment(score=0.4))

    assert store.updates == 3
    assert producer.published == []  # rien n'est encore stabilisé
    # Les trois contributions sont dans l'état, pas seulement la dernière.
    assert store.state["BTC"]["price_change_pct_24h"] == 5.0
    assert store.state["BTC"]["volume_spike_ratio"] == 3.0
    assert store.state["BTC"]["sentiment_score"] == 0.4


async def test_the_emitted_analysis_is_computed_on_the_settled_state() -> None:
    """Ce qui sort porte l'union des événements de la fenêtre, donc davantage
    d'information que n'importe lequel pris isolément."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price(chg=5.0))
    await w.handle(_volume(ratio=3.0))
    await w.handle(_sentiment(score=0.4))
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.flush_settled()

    assert len(producer.published) == 1
    a = producer.published[0]
    assert a.price_change_pct_24h == 5.0
    assert a.volume_spike_ratio == 3.0
    assert a.sentiment_score == 0.4
    # 3 facteurs sur 4 : la preuve que l'analyse n'a pas été calculée sur la
    # vue partielle du premier événement, qui n'en aurait eu que deux.
    assert a.factors_present >= 3


async def test_a_quiet_window_emits_exactly_once() -> None:
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price())
    await w.handle(_volume())
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.flush_settled()
    await w.flush_settled()  # rien de neuf : ne doit pas republier

    assert len(producer.published) == 1


async def test_the_next_collection_cycle_emits_again() -> None:
    """L'agrégation est par fenêtre, pas un verrou permanent sur le symbole."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price(chg=5.0))
    await w.handle(_volume())
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.flush_settled()

    clock.advance(60)  # cycle de collecte suivant
    await w.handle(_price(chg=9.0))
    await w.handle(_volume())
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.flush_settled()

    assert len(producer.published) == 2
    assert producer.published[1].price_change_pct_24h == 9.0


async def test_symbols_settle_independently() -> None:
    """Un symbole bavard ne doit pas retarder l'analyse d'un symbole calme."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price(symbol="BTC"))
    await w.handle(_volume(symbol="BTC"))
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.handle(_price(symbol="ETH"))  # ETH vient d'arriver, pas stabilisé
    await w.flush_settled()

    assert [a.symbol for a in producer.published] == ["BTC"]


async def test_a_continuously_active_symbol_is_still_emitted() -> None:
    """Sans plafond, un symbole recevant un événement toutes les 2 s repousserait
    son analyse indéfiniment — l'agrégation deviendrait une suppression."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price())
    await w.handle(_volume())
    for _ in range(20):
        clock.advance(worker_mod.SETTLE_S * 0.5)
        await w.handle(_price())
        await w.flush_settled()

    assert producer.published, "le plafond MAX_DELAY_S n'a jamais declenche"


async def test_an_unready_symbol_publishes_nothing() -> None:
    """Le garde-fou d'origine survit : un prix seul, sans signal, ne vaut pas
    une analyse."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price())
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.flush_settled()

    assert producer.published == []
    assert store.updates == 1  # mais l'événement a bien été enregistré


async def test_a_settled_symbol_is_forgotten_so_memory_stays_bounded() -> None:
    """Le suivi est par symbole ; sans purge après émission il grandirait avec
    l'univers de symboles pour toute la vie du processus."""
    store, producer, clock = FakeStore(), FakeProducer(), Clock()
    w = _worker(store, producer, clock)

    await w.handle(_price())
    await w.handle(_volume())
    clock.advance(worker_mod.SETTLE_S + 0.1)
    await w.flush_settled()

    assert w.pending_symbols() == 0
