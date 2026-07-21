# tests/test_trading_mode_switch.py
import asyncio

from tests.trading_helpers import load_module


def test_client_mode_follows_provider() -> None:
    kraken = load_module("kraken")
    config_mod = load_module("config")
    mode = {"m": config_mod.Mode.DRY_RUN}
    cfg = config_mod.TradingConfig(api_key="k", api_secret="c2VjcmV0")
    client = kraken.KrakenFuturesClient(cfg, mode_provider=lambda: mode["m"])
    # dry_run -> no network, simulated
    r = asyncio.run(client.send_order(
        pair="PF_SOLUSD", side="buy", order_type="lmt", size=1.0,
        limit_price=100.0, cli_ord_id="e1"))
    assert r["dry_run"] is True
    # switch to live -> base_url resolves live host
    mode["m"] = config_mod.Mode.LIVE
    assert client.current_base_url() == "https://futures.kraken.com/derivatives"
    mode["m"] = config_mod.Mode.DEMO
    assert client.current_base_url() == "https://demo-futures.kraken.com/derivatives"
