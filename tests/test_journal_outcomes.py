"""Attachement des résultats marché aux lignes de journal."""

from __future__ import annotations

from service_modules import load_service_module

japi = load_service_module("api-gateway", "journal_api")


def test_row_without_entry_price_gets_no_outcome() -> None:
    """Une analyse jamais convertie en décision n'a pas de niveaux : elle n'a
    donc pas de P&L, et surtout pas un P&L de zéro — qui tirerait toutes les
    moyennes vers le milieu et ferait passer un pipeline à l'arrêt pour neutre."""
    row = {
        "symbol": "BTC",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "sonnet_direction": None,
    }
    out = japi.attach_outcome(row, path=[(0, 100.0), (60, 110.0)], horizon="1h")
    assert out["pnl_1h"] is None
    assert out["outcome_1h"] is None


def test_row_with_levels_gets_a_simulated_outcome() -> None:
    row = {
        "symbol": "BTC",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "sonnet_direction": "long",
    }
    out = japi.attach_outcome(row, path=[(0, 100.0), (60, 111.0)], horizon="1h")
    assert out["outcome_1h"] == "take_profit"
    assert out["pnl_1h"] is not None and out["pnl_1h"] > 0


def test_empty_path_yields_null_not_zero() -> None:
    """Une ligne dont l'horizon n'est pas encore écoulé, ou dont les prix
    manquent, n'est pas une position neutre."""
    row = {
        "symbol": "BTC",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "sonnet_direction": "long",
    }
    out = japi.attach_outcome(row, path=[], horizon="4h")
    assert out["pnl_4h"] is None
    assert out["outcome_4h"] == "no_data"


def test_short_direction_is_honoured() -> None:
    """Une décision short valorisée comme un long inverserait le signe de tout
    le P&L — et donc la conclusion de l'analyse."""
    row = {
        "symbol": "BTC",
        "entry_price": 100.0,
        "stop_loss": 105.0,
        "take_profit": 90.0,
        "sonnet_direction": "short",
    }
    out = japi.attach_outcome(row, path=[(0, 100.0), (60, 89.0)], horizon="1h")
    assert out["outcome_1h"] == "take_profit"
    assert out["pnl_1h"] > 0


def test_missing_direction_defaults_to_long() -> None:
    """Le risk-engine dimensionne WATCH et LONG du même côté ; un défaut
    différent produirait un signe incohérent avec le reste du système."""
    row = {
        "symbol": "BTC",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "sonnet_direction": None,
    }
    out = japi.attach_outcome(row, path=[(0, 100.0), (60, 111.0)], horizon="1h")
    assert out["outcome_1h"] == "take_profit"


def test_original_row_is_not_mutated() -> None:
    """attach_outcome est appelé une fois par horizon sur la même ligne ; muter
    l'entrée ferait fuiter le résultat d'un horizon dans le suivant."""
    row = {
        "symbol": "BTC",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "sonnet_direction": "long",
    }
    japi.attach_outcome(row, path=[(0, 100.0), (60, 111.0)], horizon="1h")
    assert "pnl_1h" not in row


def test_horizons_accumulate_across_calls() -> None:
    """Chaque horizon ajoute ses deux colonnes sans effacer les précédentes."""
    row = {
        "symbol": "BTC",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "sonnet_direction": "long",
    }
    a = japi.attach_outcome(row, path=[(0, 100.0), (60, 111.0)], horizon="1h")
    b = japi.attach_outcome(a, path=[(0, 100.0), (60, 94.0)], horizon="4h")
    assert b["outcome_1h"] == "take_profit"
    assert b["outcome_4h"] == "stop_loss"
