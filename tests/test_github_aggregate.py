# tests/test_github_aggregate.py
from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

RepoStats = load_service_module("collector-github", "domain.activity").RepoStats
aggregate = load_service_module("collector-github", "domain.aggregate").aggregate

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def _repo(name, **kw):
    base = {
        "owner": "x",
        "repo": name,
        "stars": 1000,
        "pushed_at": datetime(2026, 8, 1, tzinfo=UTC),
        "archived": False,
        "is_fork": False,
        "commits_4w": 40,
        "commits_median_52w": 10.0,
        "pr_merged_4w": 8,
        "pr_merged_52w": 104,
        "stars_prev": 1000,
    }
    base.update(kw)
    return RepoStats(**base)


def test_sums_across_live_repos():
    a = aggregate([_repo("core"), _repo("periphery")], NOW)
    assert a.repo_count == 2
    assert a.commit_ratio_4w == 1.0  # 80 commits / (20 mediane * 4)


def test_archived_repos_are_excluded_not_zeroed():
    """Un depot archive ne tire pas la moyenne vers le bas: il sort de l'agregat."""
    a = aggregate([_repo("core"), _repo("old", archived=True, commits_4w=0)], NOW)
    assert a.repo_count == 1
    assert a.commit_ratio_4w == 1.0


def test_forks_are_excluded():
    a = aggregate([_repo("core"), _repo("mirror", is_fork=True)], NOW)
    assert a.repo_count == 1


def test_all_archived_reports_measured_zero():
    """On a regarde, tout est mort. C'est une observation, pas une absence."""
    a = aggregate([_repo("a", archived=True), _repo("b", is_fork=True)], NOW)
    assert a.repo_count == 0
    assert a.all_repos_archived is True
    assert a.commit_ratio_4w == 0.0
    # pr_ratio_4w doit recevoir le meme traitement que commit_ratio_4w: un
    # oubli sur cette seule branche laisserait passer None ici sans qu'aucun
    # test ne le remarque.
    assert a.pr_ratio_4w == 0.0


def test_no_repos_at_all_returns_none():
    """Aucun depot connu != tous les depots morts."""
    assert aggregate([], NOW) is None


def test_freshest_push_wins():
    a = aggregate(
        [
            _repo("stale", pushed_at=datetime(2026, 5, 1, tzinfo=UTC)),
            _repo("live", pushed_at=datetime(2026, 8, 1, tzinfo=UTC)),
        ],
        NOW,
    )
    assert a.days_since_push == 1


def test_pending_stats_leave_ratio_none_without_poisoning_others():
    """Un depot en 202 ne doit pas faire passer l'agregat pour mesure."""
    a = aggregate([_repo("core", commits_4w=None, commits_median_52w=None)], NOW)
    assert a.commit_ratio_4w is None
    assert a.days_since_push == 1  # les autres mesures survivent


def test_star_growth_none_when_no_previous_snapshot():
    a = aggregate([_repo("core", stars_prev=None)], NOW)
    assert a.star_growth_pct_7d is None


def test_commits_pending_with_baseline_present_stays_none():
    """Le depot est en 202 sur les commits, mais sa baseline annuelle est
    connue: le ratio doit rester absent.

    Distinct de `test_pending_stats_leave_ratio_none_without_poisoning_others`,
    qui met *aussi* `commits_median_52w=None` — ce qui fait sortir tot par la
    garde sur la baseline et ne peut pas trahir une somme vide traitee comme
    un zero mesure. Ici la baseline est presente, donc seule une somme de
    `commits_4w` correctement `None` (et pas `0`) evite un `commit_ratio_4w`
    fabrique a `0.0`.
    """
    a = aggregate([_repo("core", commits_4w=None)], NOW)
    assert a.commit_ratio_4w is None
    assert a.commit_ratio_4w != 0.0


def test_pr_pending_with_baseline_present_stays_none():
    """Meme garde que ci-dessus, cote PR: `pr_merged_4w` absent ne doit pas
    devenir un `pr_ratio_4w` mesure a zero alors que la baseline 52 semaines
    est connue."""
    a = aggregate([_repo("core", pr_merged_4w=None)], NOW)
    assert a.pr_ratio_4w is None
    assert a.pr_ratio_4w != 0.0


def test_push_timestamp_rejected_when_clock_skew_exceeds_tolerance():
    """Un horodatage dans le futur au-dela de la tolerance de gigue existe
    mais n'est pas cru: `days_since_push` doit rester `None`, et l'appelant
    doit en etre informe via `push_timestamp_rejected` pour pouvoir compter
    l'incident plutot que le laisser invisible."""
    future = NOW + timedelta(days=1)
    a = aggregate([_repo("core", pushed_at=future)], NOW)
    assert a.days_since_push is None
    assert a.push_timestamp_rejected is True


def test_push_timestamp_not_rejected_when_no_push_at_all():
    """`push_timestamp_rejected` distingue « aucun horodatage a lire » de
    « horodatage lu et rejete ». Confondre les deux ferait passer une simple
    absence de mesure pour un incident d'horloge."""
    a = aggregate([_repo("core", pushed_at=None)], NOW)
    assert a.days_since_push is None
    assert a.push_timestamp_rejected is False


def test_one_bad_clock_does_not_discard_a_healthy_repos_freshness():
    """Un depot mal date ne doit pas ecraser la fraicheur mesuree des autres
    depots du meme coin: seul le depot fautif est ecarte, pas le coin entier.

    Avant correction, `days_since_push` etait calcule sur `max(pushed_at)`
    agrege: le depot le plus mal date gagnait ce `max` et faisait retomber
    la fraicheur du coin entier a `None`, jetant la mesure correcte du depot
    sain avec elle.
    """
    skewed = _repo("skewed", pushed_at=NOW + timedelta(days=1))
    healthy = _repo("healthy", pushed_at=NOW - timedelta(days=2))
    a = aggregate([skewed, healthy], NOW)
    assert a.days_since_push == 2
    assert a.push_timestamp_rejected is True


def test_all_timestamps_rejected_reports_none():
    """Quand *tous* les depots vivants ont un horodatage rejete, il ne reste
    aucune mesure credible: la fraicheur du coin doit rester `None`, pas
    retomber sur une valeur fabriquee a partir de donnees non crues."""
    a = aggregate(
        [
            _repo("a", pushed_at=NOW + timedelta(days=1)),
            _repo("b", pushed_at=NOW + timedelta(days=2)),
        ],
        NOW,
    )
    assert a.days_since_push is None
    assert a.push_timestamp_rejected is True


def test_weighted_star_growth_partial_repos_stay_partial_not_fabricated_whole():
    """Un depot sans second releve d'etoiles exploitable ne doit ni bloquer
    la lecture des depots qui en ont, ni etre traite comme une croissance
    mesuree a 0 % qui diluerait leur taux dans la moyenne ponderee."""
    measured = _repo(
        "core",
        stars=1100,
        stars_prev=1000,
        stars_at=NOW,
        stars_prev_at=NOW - timedelta(days=7),
    )
    unmeasured = _repo(
        "docs",
        stars=50,
        stars_prev=50,
        stars_at=None,
        stars_prev_at=None,
    )
    a = aggregate([measured, unmeasured], NOW)
    # +10% sur 7 jours pile pour "core"; "docs" n'a pas de second releve et
    # doit sortir de la moyenne plutot que d'y entrer a taux 0, ce qui
    # donnerait (0.10*1000 + 0*50) / 1050 = 0.0952... au lieu de 0.10.
    assert a.star_growth_pct_7d == 0.1
