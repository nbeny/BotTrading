import asyncio
import base64

from tests.trading_helpers import load_module


def _client(mode_name):
    kraken = load_module("kraken")
    config_mod = load_module("config")
    cfg = config_mod.TradingConfig(
        mode=config_mod.Mode(mode_name),
        api_key="k",
        api_secret=base64.b64encode(b"secret-bytes").decode(),
    )
    return kraken, kraken.KrakenFuturesClient(cfg)


def test_sign_is_deterministic_and_64_bytes() -> None:
    kraken, client = _client("live")
    sig1 = client.sign("/api/v3/sendorder", "1700000000000", "orderType=lmt")
    sig2 = client.sign("/api/v3/sendorder", "1700000000000", "orderType=lmt")
    assert sig1 == sig2
    assert len(base64.b64decode(sig1)) == 64  # SHA-512 digest


def test_sign_changes_with_nonce() -> None:
    _kraken, client = _client("live")
    a = client.sign("/api/v3/sendorder", "1", "x=1")
    b = client.sign("/api/v3/sendorder", "2", "x=1")
    assert a != b


def test_base_url_per_mode() -> None:
    kraken, live = _client("live")
    _k, demo = _client("demo")
    assert live.current_base_url() == "https://futures.kraken.com/derivatives"
    assert demo.current_base_url() == "https://demo-futures.kraken.com/derivatives"


def test_dry_run_send_order_makes_no_network_call() -> None:
    kraken, client = _client("dry_run")
    result = asyncio.run(client.send_order(
        pair="PF_SOLUSD", side="buy", order_type="lmt",
        size=2.0, limit_price=150.0, cli_ord_id="evt-1",
    ))
    assert result["result"] == "success"
    assert result["order_id"].startswith("DRYRUN-")
    assert result["dry_run"] is True
