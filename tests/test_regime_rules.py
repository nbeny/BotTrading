"""Rules engine for GET /market/regime — pure, mirrors dossier.py testing style."""

from service_modules import load_service_module

regime = load_service_module("api-gateway", "regime")


def test_all_absent_yields_null_regime() -> None:
    drivers = [
        regime.funding_driver(None),
        regime.oi_delta_driver(None, None),
        regime.sentiment_driver(None, None),
        regime.dominance_driver(None, None, None),
        regime.breadth_driver(None, 0, None),
    ]
    out = regime.build_regime(drivers, computed_at="2026-08-06T00:00:00+00:00")
    assert out["regime"] is None
    assert out["confidence"] == 0.0  # zéro mesuré : 0/5 drivers présents
    assert len(out["drivers"]) == 5
    assert all(d["state"] is None and d["value"] is None for d in out["drivers"])


def test_funding_is_contrarian() -> None:
    assert regime.funding_driver(0.0002)["state"] == "bearish"  # crowded-long
    assert regime.funding_driver(-0.0002)["state"] == "bullish"  # crowded-short
    assert regime.funding_driver(0.0001)["state"] == "neutral"  # borne : strictement >
    assert regime.funding_driver(0.00005)["state"] == "neutral"


def test_oi_needs_price_direction() -> None:
    d = regime.oi_delta_driver(8.0, None)
    assert d["value"] == 8.0 and d["state"] is None  # mesuré mais non votable
    assert regime.oi_delta_driver(8.0, 2.0)["state"] == "bullish"
    assert regime.oi_delta_driver(8.0, -2.0)["state"] == "bearish"
    assert regime.oi_delta_driver(-8.0, 2.0)["state"] == "neutral"  # délevier
    assert regime.oi_delta_driver(1.0, 2.0)["state"] == "neutral"
    # délevier vote neutre quelle que soit la direction du prix
    assert regime.oi_delta_driver(-8.0, None)["state"] == "neutral"


def test_min_drivers_gate() -> None:
    two = [
        regime.funding_driver(-0.0002),
        regime.sentiment_driver(0.5, None),
        regime.oi_delta_driver(None, None),
        regime.dominance_driver(None, None, None),
        regime.breadth_driver(None, 0, None),
    ]
    assert regime.build_regime(two, computed_at="t")["regime"] is None
    assert regime.build_regime(two, computed_at="t")["confidence"] == 0.4


def test_net_vote_mapping() -> None:
    def build(states: list[str | None]) -> dict:
        keys = ["funding", "oi_delta", "market_sentiment", "btc_dominance", "breadth"]
        drivers = [
            {
                "key": k,
                "value": 1.0 if s else None,
                "state": s,
                "detail": "",
                "as_of": None,
            }
            for k, s in zip(keys, states, strict=True)
        ]
        return regime.build_regime(drivers, computed_at="t")

    assert build(["bullish"] * 3 + ["neutral"] * 2)["regime"] == "RISK_ON"
    assert (
        build(["bullish", "bullish", "neutral", "neutral", "neutral"])["regime"]
        == "ACCUMULATION"
    )
    assert (
        build(["bullish", "bearish", "neutral", "neutral", "neutral"])["regime"]
        == "NEUTRAL"
    )
    assert (
        build(["bearish", "bearish", "neutral", "neutral", "neutral"])["regime"]
        == "DISTRIBUTION"
    )
    assert build(["bearish"] * 3 + ["neutral"] * 2)["regime"] == "RISK_OFF"


def test_detail_is_auditable() -> None:
    d = regime.funding_driver(0.0002)
    assert "0.0001" in d["detail"]  # le seuil appliqué est restitué
    dom = regime.dominance_driver(54.1, 53.2, "2026-08-06T00:00:00+00:00")
    assert "univers suivi" in dom["detail"]  # l'approximation est nommée
    assert dom["value"] == 0.9  # la valeur est la dérive en points
