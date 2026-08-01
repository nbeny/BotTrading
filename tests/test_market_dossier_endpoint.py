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
    """Session factice qui compte ses appels.

    Le comptage n'est pas une coquetterie : sans lui, ces tests passent encore
    si une requête disparaît du handler — vérifié en supprimant la requête
    `content`, les trois assertions de forme restaient vertes.
    """

    def __init__(self) -> None:
        self.executed = 0

    async def execute(self, _stmt, _params=None):
        self.executed += 1
        return _Result()

    async def scalar(self, _stmt):
        return 0


# Cinq requêtes propres au dossier (journal, rejet, décisions, contenu,
# trades — `scored` est dérivé de `decisions`, plus de requête à part) plus
# trois de `_portfolio_basis` (`_account_snapshot`, puis `_open_positions` :
# trades ouverts + derniers prix). 5 + 3 = 8.
EXPECTED_QUERIES = 8


async def test_unknown_symbol_returns_an_honest_empty_dossier() -> None:
    session = _EmptySession()
    resp = await read_api.market_token_dossier(symbol="sol", session=session)

    assert resp["symbol"] == "SOL"
    assert resp["score"]["value"] is None
    assert resp["score"]["axes"] == {}
    assert resp["pipeline"]["reached_stage"] is None
    assert resp["decisions"] == []
    assert resp["content"] == []
    assert resp["exposure"]["open_positions"] == []
    assert resp["exposure"]["recent_trades"] == []


async def test_symbol_is_upper_cased() -> None:
    session = _EmptySession()
    resp = await read_api.market_token_dossier(symbol="eth", session=session)
    assert resp["symbol"] == "ETH"


async def test_the_dossier_issues_every_query_it_claims_to() -> None:
    """Cinq requêtes propres au dossier — décision, journal, rejet, contenu,
    trades — plus celles de `_portfolio_basis`, qui a les siennes. Ce test fige
    le total : le voir bouger doit être un choix, pas un accident."""
    session = _EmptySession()
    await read_api.market_token_dossier(symbol="SOL", session=session)
    assert session.executed == EXPECTED_QUERIES


def test_the_dossier_route_lives_on_the_authenticated_router() -> None:
    """``main.py`` monte ``read_api.router`` derrière ``require_principal``.

    Un déplacement ultérieur du dossier vers un routeur non authentifié
    exposerait positions et raisonnement IA à qui connaît l'URL, sans qu'aucun
    test générique d'authentification ne s'en aperçoive : ils portent sur une
    application miroir, pas sur ce routeur-ci.
    """
    paths = {getattr(r, "path", None) for r in read_api.router.routes}
    assert "/market/tokens/{symbol}/dossier" in paths
