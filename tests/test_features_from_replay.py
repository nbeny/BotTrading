"""Le mapping raw -> Features est pur, partage, et rejouable a une date donnee.

Le script de calibration doit executer exactement le mapping que la production
execute. Une seconde copie mesurerait un modele que personne ne fait tourner.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from service_modules import load_service_module

fm = load_service_module("decision-engine", "features_map")
features_from = fm.features_from

ROOT = Path(__file__).resolve().parents[1]
HAIKU_WORKER = ROOT / "services/ai-worker-haiku/app/worker.py"
NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_liquidity_falls_back_to_24h_volume() -> None:
    """liquidity_usd n'est ecrit que pour les DexEvent; une paire listee en CEX
    n'en produit jamais. Sans le repli, un dixieme du poids du modele reste
    mort."""
    assert features_from({"volume_24h_usd": 5_000_000}, now=NOW).liquidity_usd == 5e6
    assert (
        features_from(
            {"liquidity_usd": 12_000, "volume_24h_usd": 5_000_000}, now=NOW
        ).liquidity_usd
        == 12_000.0
    )


def test_unlock_days_are_measured_from_the_reference_instant() -> None:
    """Rejoue avec `now()`, un deverrouillage du 4 aout lu depuis une ligne du
    1er aout serait dans le passe et le terme disparaitrait -- alors qu'il
    valait 3 jours au moment de la decision."""
    raw = {"next_unlock_at": "2026-08-04T00:00:00+00:00", "has_unlock_schedule": True}
    assert features_from(raw, now=NOW).next_unlock_days == 3.0


def test_a_past_unlock_is_stale_not_imminent() -> None:
    """Une date passee est une lecture perimee, pas une urgence. La ramener a
    zero jour ferait lire l'axe a son *pire* la ou la verite est son meilleur."""
    later = datetime(2026, 8, 10, tzinfo=UTC)
    raw = {"next_unlock_at": "2026-08-04T00:00:00+00:00"}
    assert features_from(raw, now=later).next_unlock_days is None


def test_an_unparseable_unlock_date_does_not_raise() -> None:
    raw = {"next_unlock_at": "pas une date"}
    assert features_from(raw, now=NOW).next_unlock_days is None


def test_a_naive_unlock_date_is_read_as_utc() -> None:
    raw = {"next_unlock_at": "2026-08-03T00:00:00"}
    assert features_from(raw, now=NOW).next_unlock_days == 2.0


def test_market_sentiment_comes_from_the_row() -> None:
    assert features_from({"market_sentiment": -0.4}, now=NOW).market_sentiment == -0.4
    assert features_from({}, now=NOW).market_sentiment is None


def test_absent_flags_are_false_not_none() -> None:
    f = features_from({}, now=NOW)
    assert f.has_unlock_schedule is False
    assert f.all_repos_archived is False


def test_news_impact_is_none_when_there_is_no_news() -> None:
    """Pas 0.0: un axe absent est exclu du denominateur, un 0.0 mesure y entre
    a son pire. Les deux donnent des scores differents."""
    assert features_from({}, now=NOW).news_impact is None
    assert features_from({"has_news": True}, now=NOW).news_impact == 1.0


def test_haiku_fills_the_event_fields_from_the_same_dict() -> None:
    """features_from ne lit que le dict de features. L'invariant qui le rend
    exact est chez le producteur: les quatre champs de tete de l'AnalysisEvent
    sont remplis depuis ce meme dict. Verrouille par analyse syntaxique, comme
    test_axis_parity.py, plutot que par import: worker.py tire aiokafka."""
    tree = ast.parse(HAIKU_WORKER.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AnalysisEvent"
    ]
    assert len(calls) == 1, "un seul site construit l'AnalysisEvent"
    sources = {
        kw.arg: ast.unparse(kw.value)
        for kw in calls[0].keywords
        if kw.arg
        in {
            "price_change_pct_24h",
            "volume_spike_ratio",
            "sentiment_score",
            "social_growth",
        }
    }
    assert sources == {
        "price_change_pct_24h": "features.get('price_change_pct_24h')",
        "volume_spike_ratio": "features.get('volume_spike_ratio')",
        "sentiment_score": "features.get('sentiment_score')",
        "social_growth": "features.get('social_growth')",
    }, (
        "un champ de tete de l'AnalysisEvent ne vient plus du dict features; "
        "features_from, qui ne lit que ce dict, cesserait de rejouer la production"
    )
