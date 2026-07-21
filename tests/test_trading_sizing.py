from tests.trading_helpers import load_module


def _fn():
    return load_module("sizing").compute_size


def test_basic_notional_to_contracts() -> None:
    # equity 10_000, 4% => 400 notional; entry 100 => 4 contracts (step 0.01)
    size = _fn()(
        equity_usd=10_000, position_size_pct=0.04, entry_price=100,
        max_order_usd=500, max_leverage=3, contract_step=0.01, min_contracts=0.01,
    )
    assert size == 4.0


def test_capped_by_max_order_usd() -> None:
    # 10% of 10_000 = 1000 notional, capped at 500 => 5 contracts @ 100
    size = _fn()(
        equity_usd=10_000, position_size_pct=0.10, entry_price=100,
        max_order_usd=500, max_leverage=3, contract_step=0.01, min_contracts=0.01,
    )
    assert size == 5.0


def test_rounds_down_to_step() -> None:
    # 400 notional / 150 = 2.6667 -> step 0.1 -> 2.6
    size = _fn()(
        equity_usd=10_000, position_size_pct=0.04, entry_price=150,
        max_order_usd=500, max_leverage=3, contract_step=0.1, min_contracts=0.1,
    )
    assert size == 2.6


def test_below_min_returns_zero() -> None:
    size = _fn()(
        equity_usd=100, position_size_pct=0.01, entry_price=100,
        max_order_usd=500, max_leverage=3, contract_step=1.0, min_contracts=1.0,
    )
    assert size == 0.0
