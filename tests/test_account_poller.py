"""La boucle de sondage du compte : ce qu'elle publie, et ce qu'elle fait quand
l'exchange ne répond pas."""

from __future__ import annotations

from types import SimpleNamespace

from service_modules import load_service_module

acc = load_service_module("trading-engine", "account")


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list = []

    async def publish(self, topic, event) -> None:
        self.sent.append((topic, event))


class FakeCache:
    def __init__(self) -> None:
        self.written: dict = {}

    async def set_json(self, key, value, ttl_seconds: int = 60) -> None:
        self.written[key] = value


class FakeClient:
    def __init__(self, snap=None, error=None) -> None:
        self._snap = snap or {"equity_usd": 10.0, "cash_usd": 4.0,
                              "balances": {"ZUSD": 4.0}}
        self._error = error
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._snap


def _cfg(*, key: str, secret: str):
    return SimpleNamespace(read_api_key=key, read_api_secret=secret,
                           account_poll_s=60)


async def test_one_poll_publishes_and_caches() -> None:
    p, c = FakeProducer(), FakeCache()
    poller = acc.AccountPoller(FakeClient(), p, c, venue="kraken_spot")
    await poller.poll_once()
    _topic, event = p.sent[0]
    assert event.venue == "kraken_spot"
    assert event.equity_usd == 10.0
    assert c.written["trading:account:kraken_spot"]["equity_usd"] == 10.0


async def test_an_exchange_failure_publishes_nothing() -> None:
    """Mieux vaut pas de snapshot qu'un snapshot faux : l'absence se traduit par
    « non connecté » côté lecture, un zéro se traduirait par « vous n'avez rien »."""
    p, c = FakeProducer(), FakeCache()
    poller = acc.AccountPoller(FakeClient(error=RuntimeError("boom")), p, c,
                               venue="kraken_spot")
    await poller.poll_once()
    assert p.sent == []
    assert c.written == {}


async def test_a_failure_does_not_stop_the_loop() -> None:
    """Une clé temporairement rejetée ou un timeout ne doit pas tuer la boucle
    pour le reste de la vie du processus."""
    p, c = FakeProducer(), FakeCache()
    poller = acc.AccountPoller(FakeClient(error=RuntimeError("boom")), p, c,
                               venue="kraken_spot")
    await poller.poll_once()
    await poller.poll_once()  # ne doit pas lever


def test_no_key_means_no_provider_at_all() -> None:
    """« Aucune clé configurée → le venue est absent, pas en erreur. » Construire
    un client sans clé produirait une boucle qui échoue toutes les 60 s et remplit
    les logs pour rien."""
    assert acc.build_poller(_cfg(key="", secret=""), None, None) is None
    assert acc.build_poller(_cfg(key="k", secret=""), None, None) is None


def test_a_configured_key_produces_a_poller() -> None:
    """Le pendant du test précédent : sans lui, un `return None` inconditionnel
    passerait."""
    import base64
    poller = acc.build_poller(
        _cfg(key="k" * 56, secret=base64.b64encode(b"s" * 64).decode()), None, None
    )
    assert poller is not None
