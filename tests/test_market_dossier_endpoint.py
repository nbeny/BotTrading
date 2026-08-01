"""Handler du dossier token — session factice, pas de base.

Le cas qui compte est le symbole sans historique : il doit répondre 200 avec des
`null` honnêtes, pas 404 et pas des zéros. Un 404 dirait « ce token n'existe
pas » là où la vérité est « rien n'a encore été analysé ».
"""

from __future__ import annotations

from service_modules import load_service_module

read_api = load_service_module("api-gateway", "read_api")


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _EmptySession:
    async def execute(self, _stmt, _params=None):
        return _Result()

    async def scalar(self, _stmt):
        return 0


async def test_unknown_symbol_returns_an_honest_empty_dossier() -> None:
    resp = await read_api.market_token_dossier(symbol="sol", session=_EmptySession())

    assert resp["symbol"] == "SOL"
    assert resp["score"]["value"] is None
    assert resp["score"]["axes"] == {}
    assert resp["pipeline"]["reached_stage"] is None
    assert resp["decisions"] == []
    assert resp["content"] == []
    assert resp["exposure"]["open_positions"] == []
    assert resp["exposure"]["recent_trades"] == []


async def test_symbol_is_upper_cased() -> None:
    resp = await read_api.market_token_dossier(symbol="eth", session=_EmptySession())
    assert resp["symbol"] == "ETH"


def test_the_dossier_route_lives_on_the_authenticated_router() -> None:
    """``main.py`` monte ``read_api.router`` derrière ``require_principal``.

    Un déplacement ultérieur du dossier vers un routeur non authentifié
    exposerait positions et raisonnement IA à qui connaît l'URL, sans qu'aucun
    test générique d'authentification ne s'en aperçoive : ils portent sur une
    application miroir, pas sur ce routeur-ci.
    """
    paths = {getattr(r, "path", None) for r in read_api.router.routes}
    assert "/market/tokens/{symbol}/dossier" in paths
