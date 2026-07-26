"""Simulation d'issue de position sur le chemin de prix.

Comparer le prix d'entrée au prix final surestime systématiquement la
performance : une position stoppée à -5 %, dont le prix remonte ensuite, se
lirait comme gagnante. La simulation parcourt donc le chemin et applique la
première borne touchée.
"""

from __future__ import annotations

from service_modules import load_service_module

sim = load_service_module("api-gateway", "journal_sim")


def _path(*prices: float):
    """(offset_secondes, prix) espacés d'une minute, chronologiques."""
    return [(i * 60, p) for i, p in enumerate(prices)]


def test_long_hits_take_profit() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 104.0, 111.0, 108.0),
    )
    assert r.outcome == "take_profit"
    assert r.exit_price == 111.0
    assert r.seconds_held == 120


def test_long_hits_stop_loss() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 97.0, 94.0),
    )
    assert r.outcome == "stop_loss"
    assert r.pnl_gross_pct < 0


def test_stopped_then_recovered_is_a_loss() -> None:
    """Le cas qui justifie à lui seul le parcours de chemin. Comparer entrée et
    prix final donnerait +20 % sur une position stoppée à -6 %."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 94.0, 105.0, 120.0),
    )
    assert r.outcome == "stop_loss"
    assert r.exit_price == 94.0
    assert r.pnl_net_pct < 0


def test_short_hits_take_profit_on_the_way_down() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="short", stop_loss=105.0, take_profit=90.0,
        path=_path(100.0, 96.0, 89.0),
    )
    assert r.outcome == "take_profit"
    assert r.pnl_gross_pct > 0


def test_short_hits_stop_loss_on_the_way_up() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="short", stop_loss=105.0, take_profit=90.0,
        path=_path(100.0, 103.0, 106.0),
    )
    assert r.outcome == "stop_loss"
    assert r.pnl_gross_pct < 0


def test_neither_bound_touched_marks_to_market() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 101.0, 103.0),
    )
    assert r.outcome == "horizon"
    assert r.exit_price == 103.0


def test_empty_path_reports_no_data_not_zero() -> None:
    """Un trou de collecte doit produire une absence, jamais un P&L de zéro —
    sinon une panne de collecteur se lirait comme une position neutre."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0, path=[],
    )
    assert r.outcome == "no_data"
    assert r.pnl_net_pct is None


def test_stop_wins_when_both_bounds_fall_in_one_sample() -> None:
    """Dans un même intervalle d'échantillonnage on ne peut pas savoir laquelle
    a été touchée en premier. Supposer la favorable est la façon dont un
    backtest se ment à lui-même."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=99.0, take_profit=101.0,
        path=[(0, 100.0), (60, 100.5)],
    )
    # 100.5 ne touche ni l'une ni l'autre ; on vérifie surtout l'ordre du test
    assert r.outcome == "horizon"

    r2 = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=101.0, take_profit=101.0,
        path=[(0, 101.0)],
    )
    assert r2.outcome == "stop_loss"


def test_fees_are_charged_on_both_sides() -> None:
    """0.16 % par côté, cohérent avec read_api.map_portfolio_trade. Les ignorer
    rendrait profitable une stratégie à faible espérance."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 110.0), fee_pct=0.0016,
    )
    assert r.pnl_gross_pct == 10.0
    assert abs(r.pnl_net_pct - (10.0 - 0.32)) < 0.01


def test_a_flat_path_is_a_small_loss_after_fees() -> None:
    """Une position qui ne bouge pas perd les frais. Un simulateur qui rendrait
    zéro ici surestimerait toute stratégie à faible espérance."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 100.0, 100.0),
    )
    assert r.pnl_gross_pct == 0.0
    assert r.pnl_net_pct < 0


def test_zero_entry_price_is_no_data_not_a_division_error() -> None:
    """Un prix d'entrée absent ou nul arrive depuis des lignes de journal
    incomplètes ; il ne doit pas faire tomber une requête analytique."""
    r = sim.simulate_path(
        entry=0.0, direction="long", stop_loss=0.0, take_profit=0.0,
        path=_path(100.0, 110.0),
    )
    assert r.outcome == "no_data"
