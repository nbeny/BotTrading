"""Agrégation du résumé — pure, sans base."""

from __future__ import annotations

from service_modules import load_service_module

jq = load_service_module("api-gateway", "journal_query")


def _rows(n, *, pnl):
    return [{"pnl_net_pct": pnl} for _ in range(n)]


def test_below_minimum_sample_returns_null_not_a_number() -> None:
    """Un intervalle de confiance sur n=3 est plus dangereux qu'une absence de
    réponse : il invite à agir."""
    out = jq.compare_groups(_rows(3, pnl=5.0), _rows(3, pnl=-1.0))
    assert out["mean_a"] is None
    assert out["mean_b"] is None
    assert out["n_a"] == 3
    assert out["insufficient_sample"] is True


def test_at_minimum_sample_returns_the_comparison() -> None:
    out = jq.compare_groups(_rows(30, pnl=5.0), _rows(30, pnl=-1.0))
    assert out["insufficient_sample"] is False
    assert out["mean_a"] == 5.0
    assert out["mean_b"] == -1.0
    assert out["delta"] == 6.0


def test_one_thin_group_suppresses_the_whole_comparison() -> None:
    """Comparer 300 observations à 4 n'est pas une comparaison. Il ne suffit pas
    qu'un seul côté soit fourni."""
    out = jq.compare_groups(_rows(300, pnl=5.0), _rows(4, pnl=-1.0))
    assert out["insufficient_sample"] is True
    assert out["mean_a"] is None
    assert out["n_a"] == 300


def test_rows_without_an_outcome_are_excluded_not_counted_as_zero() -> None:
    """Une ligne non mûre n'est pas une performance nulle."""
    rows = _rows(30, pnl=4.0) + [{"pnl_net_pct": None} for _ in range(10)]
    out = jq.compare_groups(rows, _rows(30, pnl=0.0))
    assert out["n_a"] == 30
    assert out["mean_a"] == 4.0


def test_cohort_minimum_applies_per_cohort_not_globally() -> None:
    """Croiser les axes fragmente vite l'échantillon ; le plancher doit mordre
    cohorte par cohorte."""
    rows = (
        [{"cohort": "major", "pnl_net_pct": 2.0}] * 40
        + [{"cohort": "small", "pnl_net_pct": 9.0}] * 5
    )
    out = jq.by_cohort(rows, key="cohort")
    assert out["major"]["mean"] == 2.0
    assert out["small"]["mean"] is None
    assert out["small"]["n"] == 5


def test_cohort_key_missing_is_grouped_not_dropped() -> None:
    """Une ligne dont l'axe est nul appartient à une cohorte « inconnu » — la
    supprimer ferait disparaître des observations sans trace."""
    rows = [{"cohort": None, "pnl_net_pct": 1.0}] * 3
    out = jq.by_cohort(rows, key="cohort")
    assert "None" in out
    assert out["None"]["n"] == 3


def test_maturity_is_counted_per_horizon() -> None:
    """Une ligne de moins de 24 h est exploitable à +1 h et pas à +24 h. Un
    décompte global laisserait une analyse à +24 h se croire alimentée."""
    rows = [{"pnl_1h": 1.0, "pnl_24h": None} for _ in range(40)]
    assert jq.matured(rows, "pnl_1h") == 40
    assert jq.matured(rows, "pnl_24h") == 0


def test_empty_input_does_not_divide_by_zero() -> None:
    """L'état actuel du pipeline : la table est vide. Ce doit être un résultat
    ordinaire, pas une exception."""
    out = jq.compare_groups([], [])
    assert out["n_a"] == 0
    assert out["mean_a"] is None
    assert out["insufficient_sample"] is True
    assert jq.by_cohort([], key="cohort") == {}
