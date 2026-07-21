# tests/test_trade_model.py
from cmi_common.db.models import Trade


def test_trade_has_execution_columns() -> None:
    cols = Trade.__table__.columns
    assert "kraken_order_id" in cols
    assert "fill_price" in cols
    assert "pnl" in cols
    # existing status column still present
    assert "status" in cols
