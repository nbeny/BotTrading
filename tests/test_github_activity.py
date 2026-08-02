from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

_activity = load_service_module("collector-github", "domain.activity")
RepoStats = _activity.RepoStats
commit_ratio = _activity.commit_ratio
days_since_push = _activity.days_since_push
pr_ratio = _activity.pr_ratio
star_growth_pct = _activity.star_growth_pct

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def _stats(**kw):
    base = {
        "owner": "aave",
        "repo": "aave-v3-core",
        "stars": 1000,
        "forks": 100,
        "pushed_at": datetime(2026, 8, 1, tzinfo=UTC),
        "archived": False,
        "is_fork": False,
        "commits_4w": 40,
        "commits_median_52w": 10.0,
        "pr_merged_4w": 8,
        "pr_merged_52w": 104,
        "stars_prev": 990,
    }
    base.update(kw)
    return RepoStats(**base)


def test_commit_ratio_at_habitual_pace_is_one():
    # 10 commits/semaine de mediane -> 40 attendus sur 4 semaines
    assert commit_ratio(_stats(commits_4w=40, commits_median_52w=10.0)) == 1.0


def test_commit_ratio_tripled():
    assert commit_ratio(_stats(commits_4w=120, commits_median_52w=10.0)) == 3.0


def test_commit_ratio_is_none_when_stats_pending():
    """202 Accepted -> commits_4w None. None, jamais 0.0."""
    r = commit_ratio(_stats(commits_4w=None, commits_median_52w=None))
    assert r is None
    assert r != 0.0


def test_commit_ratio_is_none_when_baseline_is_zero():
    """Mediane nulle: le ratio est indefini, pas infini et pas nul."""
    assert commit_ratio(_stats(commits_4w=5, commits_median_52w=0.0)) is None


def test_commit_ratio_zero_is_measured_when_baseline_exists():
    """Un projet qui commitait et s'est arrete: 0.0 est une mesure, pas une absence."""
    assert commit_ratio(_stats(commits_4w=0, commits_median_52w=10.0)) == 0.0


def test_commit_ratio_is_none_when_numerator_pending_but_baseline_exists():
    """202 Accepted sur les commits recents seuls: None, pas 0.0, meme avec une
    mediane connue et positive (le cas non couvert par le test 202 ci-dessus,
    qui met aussi la mediane a None et masque donc ce chemin)."""
    r = commit_ratio(_stats(commits_4w=None, commits_median_52w=10.0))
    assert r is None
    assert r != 0.0


def test_pr_ratio_uses_52w_baseline():
    # 104 PR sur 52 semaines -> 2/semaine -> 8 attendues sur 4 semaines
    assert pr_ratio(_stats(pr_merged_4w=8, pr_merged_52w=104)) == 1.0


def test_pr_ratio_is_none_without_baseline():
    assert pr_ratio(_stats(pr_merged_4w=3, pr_merged_52w=None)) is None


def test_pr_ratio_is_none_when_numerator_pending_but_baseline_exists():
    """PR recentes non lues alors que la baseline 52w est connue: None, pas 0.0."""
    r = pr_ratio(_stats(pr_merged_4w=None, pr_merged_52w=104))
    assert r is None
    assert r != 0.0


def test_pr_ratio_zero_is_measured_when_baseline_exists():
    """Un depot qui mergeait des PR et s'est arrete: 0.0 est une mesure."""
    assert pr_ratio(_stats(pr_merged_4w=0, pr_merged_52w=104)) == 0.0


def test_pr_ratio_is_none_when_baseline_is_zero():
    """52 semaines sans une seule PR mergee: baseline nulle, ratio indefini."""
    assert pr_ratio(_stats(pr_merged_4w=0, pr_merged_52w=0)) is None


def test_days_since_push():
    assert (
        days_since_push(_stats(pushed_at=datetime(2026, 7, 26, tzinfo=UTC)), NOW) == 7
    )


def test_days_since_push_is_none_without_timestamp():
    assert days_since_push(_stats(pushed_at=None), NOW) is None


def test_days_since_push_is_none_when_pushed_at_is_in_the_future():
    """Horodatage futur de plusieurs jours = derive d'horloge ou lecture API
    corrompue, pas un depot hyperactif. Un clamp a 0 fabriquerait la lecture
    la plus favorable possible a partir d'une donnee a laquelle on ne peut
    pas faire confiance."""
    r = days_since_push(_stats(pushed_at=datetime(2026, 8, 10, tzinfo=UTC)), NOW)
    assert r is None
    assert r != 0


def test_days_since_push_within_clock_skew_tolerance_is_zero():
    """Une avance de 4 min 59 s reste dans la fenetre de gigue NTP: c'est un
    push tout juste arrive, pas une anomalie. 0, pas None."""
    pushed_at = NOW + timedelta(minutes=4, seconds=59)
    r = days_since_push(_stats(pushed_at=pushed_at), NOW)
    assert r == 0
    assert r is not None


def test_days_since_push_just_beyond_clock_skew_tolerance_is_none():
    """Une avance de 5 min 1 s depasse la fenetre de gigue NTP: la lecture
    n'est plus credible. None, pas 0."""
    pushed_at = NOW + timedelta(minutes=5, seconds=1)
    r = days_since_push(_stats(pushed_at=pushed_at), NOW)
    assert r is None
    assert r != 0


def test_star_growth_is_none_on_first_snapshot():
    """Un delta demande deux observations. 0.0 inventerait une stagnation."""
    g = star_growth_pct(_stats(stars=1000, stars_prev=None))
    assert g is None
    assert g != 0.0


def test_star_growth_can_be_negative():
    assert star_growth_pct(_stats(stars=990, stars_prev=1000)) == -0.01
