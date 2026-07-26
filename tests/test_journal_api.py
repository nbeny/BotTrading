"""Le résumé du journal : formes de réponse et refus de conclure."""

from __future__ import annotations

from datetime import UTC, datetime

from service_modules import load_service_module

japi = load_service_module("api-gateway", "journal_api")


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = [_Row(r) for r in rows]

    def all(self):
        return self._rows


class FakeSession:
    """Le résumé interroge maintenant deux tables : le journal, puis les prix
    une fois par ligne et par horizon. Sans distinguer les deux requêtes, la
    fausse session rendrait des lignes de journal à `price_path`."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    async def execute(self, stmt, _params=None):
        if "FROM prices" in str(stmt):
            # Aucun prix enregistré : les horizons restent `no_data`, jamais 0.
            return _Result([])
        return _Result(self._rows)


async def test_empty_journal_is_an_ordinary_response() -> None:
    """L'état actuel de la production : la table est vide. Ce doit être une
    réponse normale, pas une exception."""
    resp = await japi.journal_summary(window="7d", session=FakeSession())
    assert resp["sample"]["analyses"] == 0
    assert resp["q3_sonnet_value"]["insufficient_sample"] is True
    assert resp["q3_sonnet_value"]["mean_a"] is None


async def test_q3_compares_within_the_escalated_population() -> None:
    """Q3 doit opposer validés et rejetés PARMI les escaladés. Comparer
    escaladés et non escaladés mesurerait le gate, pas l'analyste."""
    now = datetime(2026, 7, 25, tzinfo=UTC)
    rows = (
        [{"symbol": "A", "escalated": True, "sonnet_called": True,
          "sonnet_validated": True, "risk_verdict": None, "dominant_factor": "momentum",
          "dedup_trigger": None, "market_cap_rank": 10, "confidence": 0.8,
          "time": now}] * 5
        + [{"symbol": "B", "escalated": True, "sonnet_called": True,
            "sonnet_validated": False, "risk_verdict": None,
            "dominant_factor": "volume",
            "dedup_trigger": None, "market_cap_rank": 20, "confidence": 0.6,
            "time": now}] * 5
        + [{"symbol": "C", "escalated": False, "sonnet_called": False,
            "sonnet_validated": None, "risk_verdict": None,
            "dominant_factor": "sentiment",
            "dedup_trigger": None, "market_cap_rank": 300, "confidence": 0.5,
            "time": now}] * 90
    )
    resp = await japi.journal_summary(window="30d", session=FakeSession(rows))
    assert resp["sample"]["escalated"] == 10
    assert resp["sample"]["sonnet_called"] == 10
    assert resp["sample"]["validated"] == 5
    # Q3 ne voit que les 10 escaladés, jamais les 90 autres.
    assert resp["q3_sonnet_value"]["n_a"] == 0  # aucun pnl encore calculé
    assert resp["q3_sonnet_value"]["insufficient_sample"] is True


async def test_horizons_are_configurable_and_reported() -> None:
    """Ils viennent de COUNTERFACTUAL_HORIZONS et doivent apparaître dans la
    réponse, sinon un lecteur ne sait pas ce qui a été mesuré."""
    resp = await japi.journal_summary(window="7d", session=FakeSession())
    assert resp["horizons"] == list(japi.HORIZONS)
    assert set(resp["sample"]["matured"]) == set(japi.HORIZONS)


async def test_sample_block_reports_the_minimum_required() -> None:
    """Le lecteur doit pouvoir juger la réponse sans connaître le code."""
    resp = await japi.journal_summary(window="7d", session=FakeSession())
    assert resp["sample"]["min_required"] >= 30
