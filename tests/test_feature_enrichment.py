"""Phase 1c — le collecteur mesure, le scorer juge.

Deux trous mesurés en production alimentaient le même symptôme : les majors
n'escaladaient jamais parce que deux de leurs quatre facteurs étaient
structurellement absents, pas faibles.

- `to_volume_event` n'émettait qu'au-dessus de 30 % de turnover, seuil qu'un
  BTC ne franchit jamais : le facteur volume était donc toujours manquant.
- `liquidity_usd` ne vient que de DexScreener, donc une paire listée en CEX
  retombait sur le neutre 0,5 — indiscernable d'une liquidité réellement
  médiocre.
"""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

mapper = load_service_module("collector-coingecko", "domain.mapper")
scorer = load_service_module("ai-worker-haiku", "scorer")


def _row(volume: float, mcap: float) -> dict:
    return {
        "id": "bitcoin",
        "symbol": "btc",
        "total_volume": volume,
        "market_cap": mcap,
    }


# ── le collecteur mesure ──────────────────────────────────────────────────────
def test_an_ordinary_turnover_still_produces_a_reading() -> None:
    """BTC tourne à ~2 % de sa capitalisation par jour, soit un ratio de 0,2.
    L'ancien seuil de 3,0 exigeait 30 % : le facteur volume n'existait donc
    jamais pour un major. Décider si 0,2 est intéressant revient au scorer."""
    ev = mapper.to_volume_event(_row(volume=2e10, mcap=1e12))
    assert ev is not None
    assert ev.volume_spike_ratio == 0.2


def test_a_genuine_spike_is_unchanged() -> None:
    ev = mapper.to_volume_event(_row(volume=5e11, mcap=1e12))
    assert ev is not None
    assert ev.volume_spike_ratio == 5.0


def test_no_reading_without_the_inputs_to_compute_one() -> None:
    """Absence de mesure, pas mesure à zéro : un ratio inventé serait pris pour
    un vrai par le scorer, qui compte les facteurs présents."""
    assert mapper.to_volume_event(_row(volume=0, mcap=1e12)) is None
    assert mapper.to_volume_event(_row(volume=1e9, mcap=0)) is None


# ── le scorer juge ────────────────────────────────────────────────────────────
def test_a_measured_zero_ratio_counts_as_a_supplied_factor() -> None:
    """Un turnover faible est une mesure, pas une absence. Le compter comme
    manquant redonnerait au scorer l'aveuglement que 1c corrige."""
    r = scorer.local_opportunity(
        {"price_change_pct_24h": 5.0, "volume_spike_ratio": 0.2}
    )
    assert r.factors["volume"] == 0.0
    assert r.factors_present == 2


def test_cex_liquidity_falls_back_to_the_volume_proxy() -> None:
    """Sans DexEvent, la liquidité valait un neutre 0,5 indiscernable d'une
    vraie liquidité médiocre. Le volume 24 h est déjà dans le PriceEvent."""
    r = scorer.local_opportunity({"price_change_pct_24h": 5.0, "volume_24h_usd": 2e10})
    assert r.liquidity_source == "volume_proxy"
    assert r.factors["liquidity"] > 0.5


def test_a_measured_dex_liquidity_wins_over_the_proxy() -> None:
    """Le proxy est un pis-aller ; là où DexScreener a répondu, c'est sa mesure
    qui compte."""
    r = scorer.local_opportunity(
        {"price_change_pct_24h": 5.0, "liquidity_usd": 5e5, "volume_24h_usd": 2e10}
    )
    assert r.liquidity_source == "dex"


def test_neither_source_stays_neutral_and_unknown() -> None:
    """Le comportement d'origine doit survivre là où il n'y a rien à mesurer."""
    r = scorer.local_opportunity({"price_change_pct_24h": 5.0})
    assert r.liquidity_source == "unknown"
    assert r.factors["liquidity"] == 0.5


def test_the_proxy_is_reported_as_estimated_not_measured() -> None:
    """C'est le point de `liquidity_source` : sans lui la calibration
    traiterait une liquidité estimée et une liquidité mesurée à l'identique."""
    proxy = scorer.local_opportunity(
        {"price_change_pct_24h": 5.0, "volume_24h_usd": 2e10}
    )
    dex = scorer.local_opportunity({"price_change_pct_24h": 5.0, "liquidity_usd": 2e10})
    assert proxy.factors["liquidity"] == dex.factors["liquidity"]
    assert proxy.liquidity_source != dex.liquidity_source


def test_a_zero_volume_is_not_a_liquidity_reading() -> None:
    r = scorer.local_opportunity({"price_change_pct_24h": 5.0, "volume_24h_usd": 0.0})
    assert r.liquidity_source == "unknown"
    assert r.factors["liquidity"] == 0.5


def test_decimal_volume_from_the_event_is_accepted() -> None:
    """`PriceEvent.volume_24h_usd` est un Decimal ; le scorer ne doit pas se
    casser sur le type qui lui arrive réellement."""
    r = scorer.local_opportunity(
        {"price_change_pct_24h": 5.0, "volume_24h_usd": Decimal("2e10")}
    )
    assert r.liquidity_source == "volume_proxy"
