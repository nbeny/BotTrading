from tests.trading_helpers import load_module


def test_config_defaults(monkeypatch) -> None:
    for k in [
        "TRADING_MODE", "MAX_ORDER_USD", "MAX_LEVERAGE",
        "MAX_ORDERS_PER_HOUR", "ENTRY_TIMEOUT_S", "RECONCILE_INTERVAL_S",
        "TRADING_ENABLED",
    ]:
        monkeypatch.delenv(k, raising=False)
    cfg_mod = load_module("config")
    cfg = cfg_mod.TradingConfig.from_env()
    assert cfg.mode == cfg_mod.Mode.DRY_RUN
    assert cfg.max_order_usd == 500.0
    assert cfg.max_leverage == 3.0
    assert cfg.max_orders_per_hour == 10
    assert cfg.entry_timeout_s == 30
    assert cfg.reconcile_interval_s == 10
    assert cfg.trading_enabled is True


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "demo")
    monkeypatch.setenv("MAX_ORDER_USD", "1000")
    monkeypatch.setenv("TRADING_ENABLED", "false")
    cfg_mod = load_module("config")
    cfg = cfg_mod.TradingConfig.from_env()
    assert cfg.mode == cfg_mod.Mode.DEMO
    assert cfg.max_order_usd == 1000.0
    assert cfg.trading_enabled is False
