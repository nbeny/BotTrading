"""Pure rules for GET /market/regime — no I/O, mirror of dossier.py.

Each driver votes bullish/bearish/neutral from transparent thresholds; the
`detail` string restitutes the rule and raw value so the strip's popover can
show *why*. The project's axis rule applies unchanged: an unmeasured driver is
value None / state None and is excluded — never scored neutral. Fewer than
MIN_DRIVERS measured drivers means regime None: a guessed regime is worth less
than no regime.

`confidence` in `build_regime` counts VOTABLE drivers — those with a non-None
`state` — not merely measured ones. A driver can have a value and still be
unvotable (e.g. OI delta measured but BTC price direction unknown), in which
case its `state` is None and it does not count towards confidence. This is
deliberate: a measured-but-unvotable reading lowers confidence exactly like an
absent one, because neither one contributes a vote to the regime label.
"""

from __future__ import annotations

from typing import Any

DRIVER_KEYS: tuple[str, ...] = (
    "funding",
    "oi_delta",
    "market_sentiment",
    "btc_dominance",
    "breadth",
)

#: Funding is a raw 8h fraction (0.0001 == 0.01%/8h). Distribution measured on
#: 854 Binance perps (see 2026-07-31 derivatives spec): p5 -0.000156, median
#: +0.000050, p95 +0.000159. +-0.0001 ~= 2x median - crossed often enough to be
#: a signal, rarely enough to mean crowding. The quant-cockpit spec's first
#: guess (0.0002) sits past p95 and would almost never fire.
FUNDING_CROWDED = 0.0001
OI_DELTA_PCT = 5.0
SENTIMENT_BAND = 0.2
DOMINANCE_DRIFT_PTS = 0.5
BREADTH_HIGH = 0.60
BREADTH_LOW = 0.40
MIN_DRIVERS = 3


def _driver(
    key: str,
    value: float | None,
    state: str | None,
    detail: str,
    as_of: str | None,
) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "state": state,
        "detail": detail,
        "as_of": as_of,
    }


def funding_driver(median_8h: float | None, as_of: str | None = None) -> dict[str, Any]:
    if median_8h is None:
        return _driver("funding", None, None, "médiane funding 8h indisponible", as_of)
    if median_8h > FUNDING_CROWDED:
        state, verdict = "bearish", "crowded-long"
    elif median_8h < -FUNDING_CROWDED:
        state, verdict = "bullish", "crowded-short"
    else:
        state, verdict = "neutral", "équilibré"
    detail = (
        f"médiane funding {median_8h:+.6f}/8h (Binance, univers suivi) : "
        f"{verdict}. Contrarien : > +{FUNDING_CROWDED} → bearish, "
        f"< -{FUNDING_CROWDED} → bullish."
    )
    return _driver("funding", median_8h, state, detail, as_of)


def oi_delta_driver(
    median_delta_pct: float | None,
    btc_price_change_pct: float | None,
    as_of: str | None = None,
) -> dict[str, Any]:
    if median_delta_pct is None:
        return _driver(
            "oi_delta",
            None,
            None,
            "delta OI 24h indisponible (majors seulement)",
            as_of,
        )
    if median_delta_pct > OI_DELTA_PCT:
        if btc_price_change_pct is None:
            return _driver(
                "oi_delta",
                median_delta_pct,
                None,
                f"OI +{median_delta_pct:.1f}% mais direction prix BTC "
                "inconnue : vote impossible",
                as_of,
            )
        state = "bullish" if btc_price_change_pct >= 0 else "bearish"
        verdict = "levier suit la hausse" if state == "bullish" else "build-up short"
    elif median_delta_pct < -OI_DELTA_PCT:
        state, verdict = "neutral", "délevier"
    else:
        state, verdict = "neutral", "stable"
    if btc_price_change_pct is not None:
        base = (
            f"médiane ΔOI 24h {median_delta_pct:+.1f}% (majors Binance), "
            f"prix BTC 24h {btc_price_change_pct:+.1f}%"
        )
    else:
        base = f"médiane ΔOI 24h {median_delta_pct:+.1f}% (majors Binance)"
    detail = f"{base} : {verdict}. Seuil ±{OI_DELTA_PCT}%."
    return _driver("oi_delta", median_delta_pct, state, detail, as_of)


def sentiment_driver(score: float | None, as_of: str | None) -> dict[str, Any]:
    if score is None:
        return _driver(
            "market_sentiment",
            None,
            None,
            "lecture market-wide indisponible (cadence irrégulière mesurée : "
            "médiane 19 min, p95 71 min)",
            as_of,
        )
    if score > SENTIMENT_BAND:
        state = "bullish"
    elif score < -SENTIMENT_BAND:
        state = "bearish"
    else:
        state = "neutral"
    detail = (
        f"sentiment market-wide {score:+.2f} [-1,1] (contenu crypto sans "
        f"ticker). Bande neutre ±{SENTIMENT_BAND}."
    )
    return _driver("market_sentiment", score, state, detail, as_of)


def dominance_driver(
    now_pct: float | None,
    week_ago_pct: float | None,
    as_of: str | None,
) -> dict[str, Any]:
    if now_pct is None or week_ago_pct is None:
        return _driver("btc_dominance", None, None, "dominance indisponible", as_of)
    delta = round(now_pct - week_ago_pct, 2)
    if delta > DOMINANCE_DRIFT_PTS:
        state, verdict = "bearish", "rotation vers BTC, risk-off des alts"
    elif delta < -DOMINANCE_DRIFT_PTS:
        state, verdict = "bullish", "rotation vers les alts"
    else:
        state, verdict = "neutral", "stable"
    detail = (
        f"BTC.D {now_pct:.1f}% (univers suivi ~200 tokens, pas le marché "
        f"entier), dérive 7j {delta:+.2f} pt : {verdict}. "
        f"Seuil ±{DOMINANCE_DRIFT_PTS} pt."
    )
    return _driver("btc_dominance", delta, state, detail, as_of)


def breadth_driver(
    share_positive: float | None,
    n_symbols: int,
    as_of: str | None,
) -> dict[str, Any]:
    if share_positive is None:
        return _driver("breadth", None, None, "breadth indisponible", as_of)
    if share_positive > BREADTH_HIGH:
        state = "bullish"
    elif share_positive < BREADTH_LOW:
        state = "bearish"
    else:
        state = "neutral"
    detail = (
        f"{share_positive:.0%} des {n_symbols} tokens suivis en hausse sur "
        f"24h. Seuils : > {BREADTH_HIGH:.0%} bullish, < {BREADTH_LOW:.0%} "
        "bearish."
    )
    return _driver("breadth", share_positive, state, detail, as_of)


def build_regime(drivers: list[dict[str, Any]], *, computed_at: str) -> dict[str, Any]:
    measured = [d for d in drivers if d["state"] is not None]
    confidence = round(len(measured) / len(DRIVER_KEYS), 2)
    if len(measured) < MIN_DRIVERS:
        label = None
    else:
        net = sum(1 for d in measured if d["state"] == "bullish") - sum(
            1 for d in measured if d["state"] == "bearish"
        )
        if net >= 3:
            label = "RISK_ON"
        elif net >= 1:
            label = "ACCUMULATION"
        elif net <= -3:
            label = "RISK_OFF"
        elif net <= -1:
            label = "DISTRIBUTION"
        else:
            label = "NEUTRAL"
    return {
        "regime": label,
        "confidence": confidence,
        "drivers": drivers,
        "computed_at": computed_at,
    }
