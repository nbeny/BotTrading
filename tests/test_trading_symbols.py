import pytest

from tests.trading_helpers import load_module


def test_known_symbol_maps_to_kraken_pair() -> None:
    sym = load_module("symbols")
    assert sym.to_kraken_pair("SOL") == "PF_SOLUSD"
    assert sym.to_kraken_pair("BTC") == "PF_XBTUSD"  # BTC -> XBT quirk
    assert sym.is_whitelisted("SOL") is True


def test_unknown_symbol_is_not_whitelisted() -> None:
    sym = load_module("symbols")
    assert sym.is_whitelisted("NOTACOIN") is False
    with pytest.raises(sym.UnknownSymbol):
        sym.to_kraken_pair("NOTACOIN")


def test_pair_notation_is_normalized_to_base() -> None:
    sym = load_module("symbols")
    assert sym.normalize("BTC/USDT") == "BTC"
    assert sym.normalize("SOL-PERP") == "SOL"
    assert sym.normalize("eth") == "ETH"
    # whitelist + mapping accept pair notation via normalization
    assert sym.is_whitelisted("BTC/USDT") is True
    assert sym.to_kraken_pair("SOL/USDT") == "PF_SOLUSD"
