"""Pagination du flux archivé."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from service_modules import load_service_module

api = load_service_module("api-gateway", "events_api")
cursor = load_service_module("api-gateway", "events_cursor")

T = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


class _Row:
    def __init__(self, m: dict) -> None:
        self._mapping = m


class FakeSession:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.params: dict | None = None
        self.stmt = None

    async def execute(self, _stmt, params=None):
        self.params = params
        self.stmt = _stmt

        class R:
            def __init__(self, rows):
                self._rows = [_Row(r) for r in rows]

            def all(self):
                return self._rows

        return R(self.rows)


def _row(i: int, time: datetime = T) -> dict:
    return {
        "time": time,
        "event_id": f"e{i}",
        "event_type": "PriceEvent",
        "topic": "market.price.events",
        "symbol": "BTC",
        "correlation_id": None,
        "payload": {},
    }


async def test_empty_archive_returns_an_empty_page_not_an_error() -> None:
    resp = await api.list_events(
        limit=10, types=None, symbol=None, before=None, session=FakeSession()
    )
    assert resp["items"] == []
    assert resp["next_cursor"] is None


async def test_next_cursor_is_null_on_the_last_page() -> None:
    """Sans plus de lignes que demandé, il n'y a pas de page suivante — renvoyer
    un curseur ferait boucler le client sur une page vide."""
    resp = await api.list_events(
        limit=10,
        types=None,
        symbol=None,
        before=None,
        session=FakeSession([_row(i) for i in range(10)]),
    )
    assert len(resp["items"]) == 10
    assert resp["next_cursor"] is None


async def test_extra_row_signals_a_next_page_and_is_not_returned() -> None:
    """On demande limit+1 pour savoir s'il reste quelque chose ; la ligne
    supplémentaire ne doit jamais apparaître dans les résultats."""
    resp = await api.list_events(
        limit=10,
        types=None,
        symbol=None,
        before=None,
        session=FakeSession([_row(i) for i in range(11)]),
    )
    assert len(resp["items"]) == 10
    assert resp["next_cursor"] is not None
    assert "e9" in resp["next_cursor"]


async def test_malformed_cursor_is_a_400_not_an_empty_page() -> None:
    """Renvoyer la page la plus récente ressemblerait à la fin de l'historique."""
    with pytest.raises(HTTPException) as exc:
        await api.list_events(
            limit=10,
            types=None,
            symbol=None,
            before="n'importe quoi",
            session=FakeSession(),
        )
    assert exc.value.status_code == 400


async def test_symbol_filter_is_upper_cased() -> None:
    """Les symboles sont stockés en majuscules ; un filtre sensible à la casse
    renverrait vide sans rien signaler."""
    s = FakeSession()
    await api.list_events(limit=10, types=None, symbol="btc", before=None, session=s)
    assert s.params["symbol"] == "BTC"


async def test_types_filter_is_split_and_trimmed() -> None:
    s = FakeSession()
    await api.list_events(
        limit=10,
        types=" PriceEvent , DecisionEvent ",
        symbol=None,
        before=None,
        session=s,
    )
    assert s.params["types"] == ["PriceEvent", "DecisionEvent"]


async def test_emitted_cursor_is_accepted_by_decode_even_from_a_naive_row() -> None:
    """L'archiveur écrit du naïf-UTC ; `decode` refuse un curseur naïf. Sans
    normalisation, la page 2 renverrait un 400 ressemblant à un bug client."""
    naive = T.replace(tzinfo=None)
    resp = await api.list_events(
        limit=1,
        types=None,
        symbol=None,
        before=None,
        session=FakeSession([_row(i, time=naive) for i in range(2)]),
    )
    time, event_id = cursor.decode(resp["next_cursor"])
    assert time == T
    assert event_id == "e0"


async def test_aware_row_times_survive_the_round_trip_unchanged() -> None:
    """Le cas normal (asyncpg rend du TIMESTAMPTZ conscient) ne doit pas être
    décalé par la normalisation."""
    resp = await api.list_events(
        limit=1,
        types=None,
        symbol=None,
        before=None,
        session=FakeSession([_row(i) for i in range(2)]),
    )
    assert cursor.decode(resp["next_cursor"])[0] == T


async def test_each_union_branch_is_bounded_by_its_own_limit() -> None:
    """Sans LIMIT dans chaque branche, le LIMIT externe ne s'applique qu'après
    l'union : Postgres devrait lire et trier *toutes* les lignes des deux
    hypertables d'abord — soit, sur `events_market`, les 7 jours entiers de
    prix, volumes et dex pour une requête sans filtre.

    Vérifié contre la production : le plan devient
    `Limit -> Merge Append -> Limit -> Index Scan Backward using
    events_market_pkey`, sans tri.
    """
    s = FakeSession()
    await api.list_events(limit=10, types=None, symbol=None, before=None, session=s)
    sql = " ".join(str(s.stmt).split())
    assert sql.count("ORDER BY time DESC, event_id DESC LIMIT :limit") == 3, sql
