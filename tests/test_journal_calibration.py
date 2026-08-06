"""Pure math for /systems/journal/{calibration,attribution}."""

from service_modules import load_service_module

jc = load_service_module("api-gateway", "journal_calibration")


def _rows(n: int, *, score: int = 80, pnl: float = 1.0) -> list[dict]:
    return [
        {"score": score, "pnl_4h": pnl, "factors": {"momentum": 0.5 + 0.01 * i}}
        for i in range(n)
    ]


def test_calibrate_below_min_n_reports_null_not_zero() -> None:
    out = jc.calibrate(_rows(5), threshold=70, field="pnl_4h")
    assert out["judged"] == 5 and out["sufficient"] is False
    assert out["win_rate"] is None and out["avg_pnl_pct"] is None
    assert out["total_pnl_pct"] is None


def test_calibrate_counts_and_stats() -> None:
    rows = _rows(15, pnl=2.0) + _rows(10, pnl=-1.0) + _rows(5, score=40, pnl=9.9)
    out = jc.calibrate(rows, threshold=70, field="pnl_4h")
    assert out["selected"] == 25  # les score=40 sont hors seuil
    assert out["judged"] == 25 and out["sufficient"] is True
    assert out["win_rate"] == 0.6
    assert out["total_pnl_pct"] == 20.0  # 15*2 - 10*1


def test_calibrate_ignores_unjudged_rows() -> None:
    rows = _rows(30) + [{"score": 90, "pnl_4h": None, "factors": {}}] * 10
    out = jc.calibrate(rows, threshold=70, field="pnl_4h")
    assert out["selected"] == 40 and out["judged"] == 30


def test_calibrate_min_n_boundary() -> None:
    below = jc.calibrate(_rows(19), threshold=70, field="pnl_4h")
    assert below["judged"] == 19 and below["sufficient"] is False
    assert below["win_rate"] is None
    assert below["avg_pnl_pct"] is None
    assert below["total_pnl_pct"] is None

    at = jc.calibrate(_rows(20), threshold=70, field="pnl_4h")
    assert at["judged"] == 20 and at["sufficient"] is True
    assert at["win_rate"] == 1.0
    assert at["avg_pnl_pct"] == 1.0
    assert at["total_pnl_pct"] == 20.0


def test_pearson_degenerate_is_none() -> None:
    assert jc.pearson([1.0], [1.0]) is None
    assert jc.pearson([2.0] * 30, [1.0] * 30) is None  # variance nulle


def test_attribution_positive_correlation() -> None:
    rows = [
        {
            "score": 80,
            "pnl_4h": float(i),
            "factors": {"momentum": float(i), "volume": 0.5},
        }
        for i in range(25)
    ]
    out = jc.attribution(rows, factor_keys=("momentum", "volume"), field="pnl_4h")
    momentum = next(f for f in out if f["key"] == "momentum")
    volume = next(f for f in out if f["key"] == "volume")
    assert momentum["correlation"] == 1.0 and momentum["n"] == 25
    assert volume["correlation"] is None  # variance nulle → pas de fausse mesure


def test_attribution_min_n_boundary() -> None:
    def _momentum_rows(n: int) -> list[dict]:
        return [
            {"score": 80, "pnl_4h": float(i), "factors": {"momentum": float(i)}}
            for i in range(n)
        ]

    below = jc.attribution(
        _momentum_rows(19), factor_keys=("momentum",), field="pnl_4h"
    )
    assert below[0]["n"] == 19 and below[0]["correlation"] is None

    at = jc.attribution(_momentum_rows(20), factor_keys=("momentum",), field="pnl_4h")
    assert at[0]["n"] == 20 and at[0]["correlation"] == 1.0


def test_attribution_handles_none_factors() -> None:
    rows = [
        {"score": 80, "pnl_4h": float(i), "factors": {"momentum": float(i)}}
        for i in range(20)
    ] + [{"score": 80, "pnl_4h": 1.0, "factors": None}]
    out = jc.attribution(rows, factor_keys=("momentum",), field="pnl_4h")
    assert out[0]["n"] == 20  # la ligne factors=None est ignorée, pas une erreur
