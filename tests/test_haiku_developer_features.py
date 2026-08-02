"""HaikuWorker._extract range un DeveloperEvent dans le FeatureStore.

C'est le saut que le schema du spec compressait: decision-engine ne consomme
que ANALYSIS et SENTIMENT, donc l'activite GitHub ne remonte au scoring que si
haiku la range d'abord dans son hash Redis.
"""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import DeveloperEvent, Source
from cmi_common.kafka import Topic

hw = load_service_module("ai-worker-haiku", "worker")


def _extract(event):
    # Meme construction que tests/test_haiku_extract.py: _extract est une
    # methode liee mais ne touche aucun collaborateur sur ces branches.
    worker = hw.HaikuWorker.__new__(hw.HaikuWorker)
    return worker._extract(event)


def _event(**kw):
    base = {
        "source": Source.GITHUB,
        "symbol": "AAVE",
        "coin_id": "aave",
        "repo_count": 2,
    }
    base.update(kw)
    return DeveloperEvent(**base)


def test_developer_event_maps_to_feature_fields() -> None:
    symbol, fields, topic = _extract(_event(commit_ratio_4w=1.5, days_since_push=3))
    assert symbol == "AAVE"
    assert topic == Topic.DEVELOPER.value
    assert fields["commit_ratio_4w"] == 1.5
    assert fields["days_since_push"] == 3


def test_absent_measures_stay_none() -> None:
    """Le FeatureStore ne laisse tomber que les None a la fusion: une mesure
    absente ne doit pas ecraser une lecture precedente valide."""
    _, fields, _ = _extract(_event())
    assert fields["commit_ratio_4w"] is None
    assert fields["pr_ratio_4w"] is None
    assert fields["star_growth_pct_7d"] is None


def test_all_repos_archived_false_survives_merge() -> None:
    """False est significatif ici, comme has_unlock_schedule: il dit
    "on a regarde, ce n'est pas mort"."""
    _, fields, _ = _extract(_event(all_repos_archived=False))
    assert fields["all_repos_archived"] is False


def test_the_measured_zero_survives_as_zero() -> None:
    """Tous les depots archives: commit_ratio_4w vaut 0.0, une mesure.

    Le store ne laisse tomber que les None, donc ce zero doit traverser --
    c'est le seul zero legitime de toute la chaine.
    """
    _, fields, _ = _extract(
        _event(repo_count=0, all_repos_archived=True, commit_ratio_4w=0.0)
    )
    assert fields["commit_ratio_4w"] == 0.0
    assert fields["all_repos_archived"] is True


def test_developer_event_does_not_trigger_scoring() -> None:
    """Contexte, pas declencheur: scorer un symbole dont on n'a pas le prix
    inventerait une opportunite a partir d'une statistique de depot.

    _ready exige has_market ET has_signal; aucune cle developer_* n'appartient
    a l'une ou l'autre liste, donc la propriete tient par construction. Ce
    test la fige contre un elargissement futur.
    """
    assert hw.HaikuWorker._ready({"commit_ratio_4w": 1.5}) is False
    assert hw.HaikuWorker._ready({"dev_repo_count": 3}) is False


def test_developer_features_do_not_rescue_a_symbol_without_price() -> None:
    """Meme melangees a un signal, elles ne remplacent pas la donnee marche."""
    assert hw.HaikuWorker._ready({"sentiment_score": 0.5, "commit_ratio_4w": 2.0}) is (
        False
    )
