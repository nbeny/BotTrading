"""`kraken_balance_usd` : un vrai solde, ou rien — jamais un nombre inventé.

Le champ valait `cash x 0,8`, une constante multipliée par un capital lui-même
imaginaire. Ces tests fixent la règle inverse : sans instantané de l'exchange,
le plan de lecture dit « je ne sais pas », il n'invente pas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

read_api = load_service_module("api-gateway", "read_api")

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _snap(**kw):
    base = {
        "venue": "kraken_spot",
        "equity_usd": 1234.56,
        "cash_usd": 1000.0,
        "fetched_at": NOW,
    }
    base.update(kw)
    return base


def test_without_a_snapshot_the_balance_is_null_and_says_so() -> None:
    """Un nombre inventé est pire qu'une absence : l'opérateur ne peut pas
    distinguer « je n'ai rien » de « je ne suis pas connecté »."""
    p = read_api.compute_portfolio([], 0.0, snapshot=None, now=NOW)
    assert p["kraken_balance_usd"] is None
    assert p["balance_source"] == "unavailable"
    assert p["balance_stale"] is False
    assert p["balance_fetched_at"] is None


def test_a_fresh_snapshot_is_served_as_is() -> None:
    p = read_api.compute_portfolio(
        [], 0.0, snapshot=_snap(fetched_at=NOW - timedelta(seconds=30)), now=NOW
    )
    assert p["kraken_balance_usd"] == 1234.56
    assert p["balance_source"] == "kraken_spot"
    assert p["balance_stale"] is False
    assert p["balance_fetched_at"] is not None


def test_a_snapshot_older_than_five_minutes_is_flagged_stale_not_dropped() -> None:
    """Une valeur périmée reste plus informative qu'un vide — à condition d'être
    annoncée comme périmée, sinon elle passe pour fraîche."""
    p = read_api.compute_portfolio(
        [], 0.0, snapshot=_snap(fetched_at=NOW - timedelta(minutes=6)), now=NOW
    )
    assert p["kraken_balance_usd"] == 1234.56
    assert p["balance_stale"] is True


def test_staleness_survives_a_naive_snapshot_timestamp() -> None:
    """Le persister écrit du naïf-UTC. Comparer un datetime naïf à un `now`
    conscient lève un TypeError ; le faire passer pour frais serait pire encore,
    puisque le solde périmé s'afficherait comme courant."""
    p = read_api.compute_portfolio(
        [],
        0.0,
        snapshot=_snap(fetched_at=(NOW - timedelta(minutes=6)).replace(tzinfo=None)),
        now=NOW,
    )
    assert p["balance_stale"] is True


def test_a_real_snapshot_becomes_the_reference_capital() -> None:
    """Sans cela, un vrai solde Kraken s'afficherait à côté de positions
    dimensionnées sur un capital imaginaire de 100 000 $."""
    assert read_api.reference_capital(_snap(equity_usd=5000.0)) == 5000.0


def test_without_a_snapshot_the_configured_capital_is_the_fallback() -> None:
    assert read_api.reference_capital(None) == read_api.BASE_CAPITAL


def test_a_stale_snapshot_still_governs_the_capital() -> None:
    """Un solde d'il y a dix minutes reste une bien meilleure approximation du
    capital réel que la constante de configuration."""
    stale = _snap(equity_usd=5000.0, fetched_at=NOW - timedelta(minutes=10))
    assert read_api.reference_capital(stale) == 5000.0


def test_an_empty_account_is_not_treated_as_no_account() -> None:
    """Un solde de zéro est une réponse, pas une absence. Retomber sur les
    100 000 $ de configuration ferait dimensionner des positions sur un capital
    que l'opérateur n'a pas."""
    assert read_api.reference_capital(_snap(equity_usd=0.0)) == 0.0


def test_the_reference_capital_reaches_the_positions_not_only_the_header() -> None:
    """Le solde affiché et les positions doivent être dimensionnés sur le même
    capital, sinon le total du portefeuille ne correspond pas à son en-tête."""
    from types import SimpleNamespace

    trade = SimpleNamespace(
        event_id="t1", symbol="BTC", direction="long", entry_price=100.0,
        position_size_pct=0.1, stop_loss=None, take_profit=None,
        fill_price=None, status="filled", pnl=None, created_at=NOW,
    )
    on_snapshot = read_api.map_position(trade, None, base_capital=5000.0)
    on_default = read_api.map_position(trade, None)
    assert on_snapshot["quantity"] == 5.0  # 10 % de 5 000 $ à 100 $
    assert on_snapshot["quantity"] != on_default["quantity"]
