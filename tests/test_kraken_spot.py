"""Client Kraken spot : signature, parsing, et le mode d'échec qui compte."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from service_modules import load_service_module

ks = load_service_module("trading-engine", "kraken_spot")

KEY = "k" * 56
SECRET = base64.b64encode(b"s" * 64).decode()


def test_signature_matches_the_spot_scheme_not_the_futures_one() -> None:
    """Recopier le schéma Futures produirait une signature refusée. On recalcule
    la référence à la main plutôt que de figer une constante opaque."""
    c = ks.KrakenSpotClient(KEY, SECRET)
    path, nonce, postdata = "/0/private/Balance", "1700000000000", "nonce=1700000000000"
    expected = base64.b64encode(
        hmac.new(
            base64.b64decode(SECRET),
            path.encode() + hashlib.sha256((nonce + postdata).encode()).digest(),
            hashlib.sha512,
        ).digest()
    ).decode()
    assert c.sign(path, nonce, postdata) == expected


def test_nonce_never_goes_backwards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kraken rejette définitivement un nonce inférieur au dernier vu. L'horloge
    est figée pour que tous les appels tombent dans le même instant : c'est
    précisément le cas que l'horloge seule ne couvre pas. Le volume (> 1000)
    est délibéré — un compteur cyclique passerait un échantillon plus court."""
    c = ks.KrakenSpotClient(KEY, SECRET)
    monkeypatch.setattr(ks.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    nonces = [int(c._nonce()) for _ in range(1500)]
    assert nonces == sorted(nonces)
    assert len(set(nonces)) == 1500


def test_an_api_error_is_raised_even_though_kraken_answers_200() -> None:
    """Kraken renvoie HTTP 200 avec `{"error": ["EAPI:Invalid key"]}`. Sans ce
    contrôle, `result` serait vide et un solde de 0 s'afficherait comme un vrai
    solde — pire que pas de solde du tout."""
    with pytest.raises(ks.KrakenApiError, match="EAPI:Invalid key"):
        ks.unwrap({"error": ["EAPI:Invalid key"], "result": {}})


def test_unwrap_returns_the_result_when_there_is_no_error() -> None:
    assert ks.unwrap({"error": [], "result": {"ZUSD": "10.5"}}) == {"ZUSD": "10.5"}


def test_balances_are_parsed_from_strings_and_dust_is_dropped() -> None:
    """Kraken renvoie des montants en chaînes. Les comptes traînent des poussières
    à 1e-9 qui n'apportent rien et allongent le payload."""
    out = ks.parse_balances(
        {"ZUSD": "1000.5000", "XXBT": "0.0100", "XETH": "0.00000000"}
    )
    assert out == {"ZUSD": 1000.5, "XXBT": 0.01}


def test_equity_and_cash_come_from_the_two_endpoints() -> None:
    """`eb` de TradeBalance est le compte entier valorisé en USD ; le solde ZUSD
    de Balance est le cash seul. Confondre les deux afficherait la totalité du
    portefeuille comme si elle était disponible."""
    snap = ks.build_snapshot(
        trade_balance={"eb": "1234.5678", "tb": "1200.0"},
        balances={"ZUSD": "1000.0", "XXBT": "0.01"},
    )
    assert snap["equity_usd"] == 1234.5678
    assert snap["cash_usd"] == 1000.0
    assert snap["balances"] == {"ZUSD": 1000.0, "XXBT": 0.01}


def test_a_missing_quote_balance_means_zero_cash_not_a_crash() -> None:
    """Un compte entièrement investi n'a pas de ligne ZUSD."""
    snap = ks.build_snapshot(trade_balance={"eb": "500.0"}, balances={"XXBT": "0.01"})
    assert snap["cash_usd"] == 0.0
    assert snap["equity_usd"] == 500.0


def test_a_usdc_only_account_is_not_reported_as_having_no_cash() -> None:
    """Mesuré sur le compte réel : tout le solde est en USDC, Kraken n'a aucune
    ligne ZUSD. Ne compter que ZUSD rendait `cash_usd: 0.0` à côté d'une équité
    de 97,21 — ce qui se lit « entièrement investi » alors que rien ne l'est."""
    snap = ks.build_snapshot(
        trade_balance={"eb": "97.2072"}, balances={"USDC": "97.207294"}
    )
    assert snap["equity_usd"] == 97.2072
    assert snap["cash_usd"] == 97.207294


def test_cash_sums_across_the_dollar_pegs() -> None:
    snap = ks.build_snapshot(
        trade_balance={"eb": "300.0"},
        balances={"ZUSD": "100.0", "USDC": "150.0", "XXBT": "0.01"},
    )
    assert snap["cash_usd"] == 250.0
    assert snap["balances"]["XXBT"] == 0.01


def test_a_coin_only_account_still_reports_no_cash() -> None:
    """Le pendant : sans stablecoin, zéro est la bonne réponse, pas un repli."""
    snap = ks.build_snapshot(trade_balance={"eb": "500.0"}, balances={"XXBT": "0.01"})
    assert snap["cash_usd"] == 0.0
