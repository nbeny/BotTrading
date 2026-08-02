# GitHub — activité développeur — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mesurer l'activité de développement GitHub par token et l'exposer comme huitième axe de scoring `developer_activity`.

**Architecture:** Un service `collector-github` publie un `DeveloperEvent` sur `market.developer.events`. `ai-worker-haiku` le range dans le `FeatureStore` Redis, `decision-engine` le lit depuis `AnalysisEvent.meta["features"]` et le score. Le service tourne sur deux horloges : publication depuis le cache toutes les 600 s (le `FeatureStore` expire à 900 s), rafraîchissement GitHub en round-robin sur 12 h.

**Tech Stack:** Python 3.12, FastAPI, httpx, SQLAlchemy 2.0 async, Alembic, aiokafka, Pydantic v2, pytest.

**Spec :** `docs/superpowers/specs/2026-08-02-github-ingestion-design.md`

---

## Écart assumé par rapport au spec

Le spec listait `commit_momentum` / `pr_momentum` dans `DeveloperEvent`, ce qui plaçait la
mise à l'échelle `[0, 1]` dans le collector. Ce plan la déplace dans `scoring.py`, où vit la
mise à l'échelle des sept autres axes, et l'événement transporte des **ratios bruts** :
`commit_ratio_4w`, `pr_ratio_4w`, `days_since_push`, `star_growth_pct_7d`. Même information,
même formule, mais la frontière collector/scoring reste identique à celle de DefiLlama et
Binance.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `libs/cmi_common/cmi_common/events/base.py` | `EventType.DEVELOPER`, `Source.GITHUB` |
| `libs/cmi_common/cmi_common/events/market.py` | `DeveloperEvent` |
| `libs/cmi_common/cmi_common/kafka/topics.py` | `Topic.DEVELOPER`, `TOPIC_EVENT` |
| `libs/cmi_common/cmi_common/db/models.py` | 3 tables du registre et des snapshots |
| `migrations/alembic/versions/0017_github_activity.py` | migration |
| `services/collector-github/app/domain/activity.py` | ratios purs par dépôt |
| `services/collector-github/app/domain/aggregate.py` | agrégat multi-dépôts par coin |
| `services/collector-github/app/domain/lists.py` | parsing pur des deux README |
| `services/collector-github/app/infrastructure/github_client.py` | REST GitHub, deux seaux de quota |
| `services/collector-github/app/infrastructure/lists_client.py` | téléchargement des README |
| `services/collector-github/app/infrastructure/repo_map.py` | `coin → repos` (CoinGecko + promotion) |
| `services/collector-github/app/infrastructure/store.py` | lecture/écriture des 3 tables |
| `services/collector-github/app/application/collector.py` | le cycle deux horloges |
| `services/collector-github/app/main.py` | FastAPI + `run_periodic` |
| `services/ai-worker-haiku/app/{main,worker}.py` | consommation du nouveau topic |
| `services/decision-engine/app/scoring.py` | axe + poids rescalés |
| `services/api-gateway/app/dossier.py` | `AXIS_KEYS` |
| `frontend/src/lib/types/dossier.ts` | `SCORE_AXES`, `AXIS_LABELS` |
| `tests/test_axis_parity.py` | garde-fou sur les trois copies |
| `scripts/verify_github_activity.py` | harnais live |

Les tests vivent à la racine dans `tests/`, comme `tests/test_defillama_client.py`.

**Import des modules de service dans les tests — obligatoire.** Chaque service embarque un
package littéralement nommé `app`, et `tests/conftest.py` porte un garde qui **fait échouer
la collecte** si l'un d'eux est importé sous ce nom nu. Tout accès à du code de service passe
donc par `service_modules.load_service_module("<service>", "<module.dotted>")`, qui enregistre
le package sous un alias unique. Un `from app.domain.activity import ...` casse toute la suite,
pas seulement le fichier fautif.

```python
from service_modules import load_service_module

_activity = load_service_module("collector-github", "domain.activity")
RepoStats = _activity.RepoStats
```

---

## Task 1 : Contrat d'événement

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/base.py` (enum `EventType`, enum `Source`)
- Modify: `libs/cmi_common/cmi_common/events/market.py`
- Modify: `libs/cmi_common/cmi_common/kafka/topics.py`
- Test: `tests/test_developer_event.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_developer_event.py
import pytest
from pydantic import ValidationError

from cmi_common.events import DeveloperEvent, EventType, Source, parse_event
from cmi_common.kafka import TOPIC_EVENT, Topic


def test_measures_default_to_none_not_zero():
    """Un champ absent doit rester None: 0.0 affirmerait une mesure."""
    e = DeveloperEvent(source=Source.GITHUB, symbol="AAVE", coin_id="aave", repo_count=3)
    assert e.commit_ratio_4w is None
    assert e.pr_ratio_4w is None
    assert e.days_since_push is None
    assert e.star_growth_pct_7d is None
    assert e.all_repos_archived is False
    assert e.event_type == EventType.DEVELOPER


def test_round_trip_through_the_discriminated_union():
    """Le round-trip doit passer par parse_event, pas par model_validate_json.

    Un evenement absent de AnyEvent publie parfaitement et echoue a la
    consommation, parse_event levant sur le discriminant — c'est ainsi que
    JournalEntryEvent est parti casse. model_validate_json contourne l'union
    et passerait encore si DeveloperEvent en etait retire, donc il ne teste
    pas ce qui casse.
    """
    e = DeveloperEvent(
        source=Source.GITHUB, symbol="AAVE", coin_id="aave",
        repo_count=2, commit_ratio_4w=1.5, days_since_push=3,
    )
    back = parse_event(e.as_kafka_value())
    assert isinstance(back, DeveloperEvent)
    assert back.commit_ratio_4w == 1.5
    assert back.pr_ratio_4w is None
    assert back.days_since_push == 3


def test_events_partition_by_symbol():
    """BaseEvent.partition_key() rend un UUID neuf: sans surcharge, les
    evenements d'un meme token s'eparpillent sur les 3 partitions et l'ordre
    par symbole est perdu."""
    e = DeveloperEvent(source=Source.GITHUB, symbol="AAVE", coin_id="aave", repo_count=1)
    assert e.partition_key() == "AAVE"


def test_a_measured_zero_and_an_absent_measure_stay_distinct():
    """all_repos_archived=True + repo_count=0 veut dire « on a regarde, tout
    est mort ». Toutes les mesures a None veut dire « on n'a pas regarde ».
    Les confondre est la classe de defaut la plus couteuse de ce depot."""
    dead = parse_event(
        DeveloperEvent(
            source=Source.GITHUB, symbol="AAVE", coin_id="aave",
            repo_count=0, all_repos_archived=True,
        ).as_kafka_value()
    )
    unknown = parse_event(
        DeveloperEvent(
            source=Source.GITHUB, symbol="AAVE", coin_id="aave", repo_count=2
        ).as_kafka_value()
    )
    assert dead.all_repos_archived is True and dead.repo_count == 0
    assert unknown.all_repos_archived is False
    assert unknown.commit_ratio_4w is None


def test_topic_is_registered():
    assert Topic.DEVELOPER == "market.developer.events"
    assert TOPIC_EVENT[Topic.DEVELOPER] is DeveloperEvent


def test_ratios_reject_negatives():
    """Un ratio est un rapport de comptages: negatif = bug amont, pas une mesure."""
    with pytest.raises(ValidationError):
        DeveloperEvent(
            source=Source.GITHUB, symbol="AAVE", coin_id="aave",
            repo_count=1, commit_ratio_4w=-0.5,
        )
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_developer_event.py -v`
Expected: FAIL — `ImportError: cannot import name 'DeveloperEvent'`

- [ ] **Step 3 : implémenter**

Dans `libs/cmi_common/cmi_common/events/base.py`, ajouter à `EventType` après `FUNDAMENTALS` :

```python
    DEVELOPER = "DeveloperEvent"
```

et à `Source`, après `BINANCE_FUTURES` :

```python
    GITHUB = "github"
```

Dans `libs/cmi_common/cmi_common/events/market.py`, après `FundamentalsEvent` :

```python
class DeveloperEvent(BaseEvent):
    """Activité de développement agrégée par token, sur ``market.developer.events``.

    Tous les champs de mesure transportent des **ratios bruts**, pas des valeurs
    mises à l'échelle : la mise à l'échelle vit dans ``decision-engine`` comme
    pour les sept autres axes.

    ``all_repos_archived`` est le seul zéro légitime de cette chaîne. Il dit
    « on a regardé, tous les dépôts connus sont archivés ou forkés » — une
    observation, à distinguer d'une absence de mesure, qui reste ``None``.
    """

    event_type: Literal[EventType.DEVELOPER] = EventType.DEVELOPER
    symbol: str
    coin_id: str = Field(
        ...,
        description=(
            "CoinGecko id, e.g. 'aave' — joint directement Token.coin_id, comme "
            "FundamentalsEvent."
        ),
    )
    #: Dépôts retenus dans l'agrégat (archivés et forks exclus). Un décompte,
    #: pas une mesure. ``0`` est légal et n'accompagne qu'un cas :
    #: ``all_repos_archived=True``, où il dit « des dépôts existent, aucun n'est
    #: vivant ». Quand *aucun* dépôt n'a pu être lu, le collector ne publie pas
    #: d'événement du tout, plutôt qu'un événement à zéro.
    repo_count: int = Field(..., ge=0)
    #: commits sur 4 semaines / (médiane hebdomadaire sur 52 semaines × 4).
    #: 1.0 = rythme habituel. Borné en bas à 0 : c'est un rapport de comptages.
    commit_ratio_4w: float | None = Field(default=None, ge=0)
    pr_ratio_4w: float | None = Field(default=None, ge=0)
    #: Jours depuis le push le plus récent, tous dépôts confondus.
    days_since_push: int | None = Field(default=None, ge=0)
    #: Croissance des étoiles sur 7 jours, en fraction (0.02 = +2 %). Peut être
    #: négative : un dépôt perd des étoiles.
    star_growth_pct_7d: float | None = None
    all_repos_archived: bool = False

    @model_validator(mode="after")
    def _validate_repo_count(self) -> "DeveloperEvent":
        """``repo_count=0`` n'a de sens qu'accompagné de ``all_repos_archived``.

        Rejeté à la construction **et au décodage** — pydantic exécute les
        validateurs ``mode="after"`` dans ``validate_python``, donc un message
        forgé sur le topic échoue au lieu de se décoder en zéro fabriqué.
        Même position que ``DerivativesEvent.long_short_account_ratio`` :
        rejeté à la construction, pas dans le scorer.

        **Contourné par ``model_copy(update=...)``**, qui ne revalide pas. Le
        collector republie des événements en cache toutes les 600 s, ce qui est
        une invitation permanente à y recourir — `collector-binance-futures`
        s'est déjà fait piéger exactement là.
        """
        if self.repo_count == 0 and not self.all_repos_archived:
            raise ValueError(
                "repo_count=0 exige all_repos_archived=True — sans dépôt lu du "
                "tout, ne publiez pas d'événement"
            )
        return self
```

Exporter `DeveloperEvent` dans `libs/cmi_common/cmi_common/events/__init__.py` à côté de `FundamentalsEvent`.

Dans `libs/cmi_common/cmi_common/kafka/topics.py`, ajouter l'import de `DeveloperEvent`, puis dans `Topic` après `FUNDAMENTALS` :

```python
    DEVELOPER = "market.developer.events"
```

dans `TOPIC_EVENT` :

```python
    Topic.DEVELOPER: DeveloperEvent,
```

et dans `TOPIC_PARTITIONS` — **obligatoire, pas optionnel** :

```python
    Topic.DEVELOPER: 3,
```

`tests/test_journal_topic.py::test_every_topic_appears_in_both_tables` impose
`set(TOPIC_EVENT) == set(Topic)` **et** `set(TOPIC_PARTITIONS) == set(Topic)` : l'oubli
fait échouer toute la suite. La raison est écrite dans le dépôt — un topic absent de l'une
des deux tables publie sans erreur et casse à la consommation, ce qui est la manière dont
`JournalEntryEvent` est parti cassé en production.

Surcharger enfin `partition_key()` sur `DeveloperEvent` pour rendre `self.symbol`, comme
tous les événements portant un symbole. `BaseEvent.partition_key()` rend un UUID neuf par
défaut : sans la surcharge, les événements d'un même token s'éparpillent sur les trois
partitions et l'ordre par symbole est perdu.

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Ajouter enfin quatre tests que les revues ont exigés, tous de la même famille : ils
vérifient une affirmation sur la mesure plutôt que de redire le modèle.

- `test_zero_repos_requires_all_archived` — le validateur rejette bien la combinaison ;
- `test_wire_payload_rejects_zero_repos_without_all_archived` — même rejet sur un payload
  JSON forgé à la main passé à `parse_event`, ce qui est une affirmation différente de
  « notre constructeur est prudent » : elle porte sur un message tiers ;
- `test_ratios_reject_negatives` **paramétré** sur `commit_ratio_4w`, `pr_ratio_4w` et
  `days_since_push` — chaque borne testée individuellement, sinon deux d'entre elles
  survivent à leur suppression ;
- `test_star_growth_accepts_negatives` — l'absence de borne sur `star_growth_pct_7d` est
  **délibérée** et doit être protégée : ajouter un `ge=0` ici rejouerait l'incident
  `fees_24h_usd`, où un `ge=0` sur des frais légitimement négatifs levait dans une boucle
  d'émission sans garde et faisait publier zéro événement pour tous les tokens du cycle.

Run: `python -m pytest tests/test_developer_event.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5 : commit**

```bash
git add libs/cmi_common/cmi_common/events/ libs/cmi_common/cmi_common/kafka/topics.py tests/test_developer_event.py
git commit -m "feat(events): DeveloperEvent sur market.developer.events"
```

---

## Task 2 : Ratios purs par dépôt

**Files:**
- Create: `services/collector-github/app/domain/activity.py`
- Create: `services/collector-github/app/__init__.py`, `app/domain/__init__.py`
- Create: `services/collector-github/pyproject.toml`
- Test: `tests/test_github_activity.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_activity.py
from datetime import UTC, datetime

from service_modules import load_service_module

_activity = load_service_module("collector-github", "domain.activity")
RepoStats = _activity.RepoStats
commit_ratio = _activity.commit_ratio
days_since_push = _activity.days_since_push
pr_ratio = _activity.pr_ratio
star_growth_pct = _activity.star_growth_pct

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def _stats(**kw):
    base = dict(
        owner="aave", repo="aave-v3-core", stars=1000, forks=100,
        pushed_at=datetime(2026, 8, 1, tzinfo=UTC), archived=False, is_fork=False,
        commits_4w=40, commits_median_52w=10.0, pr_merged_4w=8, pr_merged_52w=104,
        stars_prev=990,
    )
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


def test_pr_ratio_uses_52w_baseline():
    # 104 PR sur 52 semaines -> 2/semaine -> 8 attendues sur 4 semaines
    assert pr_ratio(_stats(pr_merged_4w=8, pr_merged_52w=104)) == 1.0


def test_pr_ratio_is_none_without_baseline():
    assert pr_ratio(_stats(pr_merged_4w=3, pr_merged_52w=None)) is None


def test_days_since_push():
    assert days_since_push(_stats(pushed_at=datetime(2026, 7, 26, tzinfo=UTC)), NOW) == 7


def test_days_since_push_is_none_without_timestamp():
    assert days_since_push(_stats(pushed_at=None), NOW) is None


def test_star_growth_is_none_on_first_snapshot():
    """Un delta demande deux observations. 0.0 inventerait une stagnation."""
    g = star_growth_pct(_stats(stars=1000, stars_prev=None))
    assert g is None
    assert g != 0.0


def test_star_growth_can_be_negative():
    assert star_growth_pct(_stats(stars=990, stars_prev=1000)) == -0.01
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_activity.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `domain.activity` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/pyproject.toml` :

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "collector-github"
version = "0.1.0"
description = "GitHub collector — activité de développement par token"
requires-python = ">=3.12"
dependencies = ["cmi-common", "httpx>=0.27", "sqlalchemy>=2.0", "asyncpg>=0.29"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

`services/collector-github/app/__init__.py` et `app/domain/__init__.py` : fichiers vides.

`services/collector-github/app/domain/activity.py` :

```python
"""Ratios d'activité par dépôt — pur, synchrone, sans I/O.

Chaque fonction rapporte une mesure ou ``None``. Jamais un zéro de remplacement :
en aval, un axe absent est *exclu* de la renormalisation tandis qu'un axe mesuré
mauvais tire le score vers le bas. Un ``0.0`` fabriqué ici est donc une opinion
négative déguisée en observation.

Le seul zéro légitime est un zéro constaté : un dépôt dont la baseline annuelle
est positive et qui n'a rien produit en quatre semaines s'est réellement arrêté.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: Fenêtre récente, en semaines. La baseline est ramenée à cette même durée.
WINDOW_WEEKS = 4
WEEKS_PER_YEAR = 52


@dataclass(frozen=True, slots=True)
class RepoStats:
    """Ce qu'un cycle a pu lire d'un dépôt. Toute mesure est optionnelle."""

    owner: str
    repo: str
    stars: int | None = None
    forks: int | None = None
    pushed_at: datetime | None = None
    archived: bool = False
    is_fork: bool = False
    #: None tant que GitHub répond 202 sur /stats/commit_activity.
    commits_4w: int | None = None
    commits_median_52w: float | None = None
    pr_merged_4w: int | None = None
    pr_merged_52w: int | None = None
    #: Étoiles au snapshot précédent. None au premier passage.
    stars_prev: int | None = None


def _ratio(recent: int | None, expected: float | None) -> float | None:
    if recent is None or expected is None or expected <= 0:
        # expected <= 0 : le ratio est indéfini. Le rendre infini (ou 1.0 au
        # motif que « tout commit est une accélération ») inventerait une
        # lecture à partir d'une division impossible.
        return None
    return recent / expected


def commit_ratio(stats: RepoStats) -> float | None:
    """Commits des 4 dernières semaines rapportés au rythme habituel du dépôt.

    1.0 = le projet avance à sa vitesse de croisière annuelle. La médiane
    hebdomadaire, et non la moyenne, parce qu'un unique gros merge (import de
    vendor, reformatage) écrase une moyenne et rendrait tout le reste de l'année
    anormalement calme.
    """
    if stats.commits_median_52w is None:
        return None
    return _ratio(stats.commits_4w, stats.commits_median_52w * WINDOW_WEEKS)


def pr_ratio(stats: RepoStats) -> float | None:
    """PR mergées sur 4 semaines rapportées à la moyenne des 52 dernières."""
    if stats.pr_merged_52w is None:
        return None
    weekly = stats.pr_merged_52w / WEEKS_PER_YEAR
    return _ratio(stats.pr_merged_4w, weekly * WINDOW_WEEKS)


#: Tolérance de gigue NTP entre notre horloge et celle de GitHub — pas une
#: règle métier. En dessous, un horodatage « dans le futur » est notre horloge
#: qui retarde ; au-delà, c'est une lecture qu'on ne croit pas.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


def days_since_push(stats: RepoStats, now: datetime) -> int | None:
    """Jours depuis le dernier push, ``None`` si l'horodatage est incroyable.

    Un ``max(0, ...)` seul serait un piège : ``timedelta.days`` arrondit vers
    moins l'infini, donc un push en avance d'**une seconde** donne ``.days ==
    -1``. Clamper à 0 traduirait une horloge décalée en « poussé aujourd'hui »,
    c'est-à-dire la valeur de fraîcheur la plus favorable qui soit — un zéro
    fabriqué, exactement ce que ce module existe pour empêcher.

    Rendre ``None`` dès la première seconde d'avance serait le défaut inverse :
    le sous-signal disparaîtrait sans bruit sur les dépôts qui viennent de
    pousser, c'est-à-dire les plus actifs, ceux que le momentum cherche
    précisément à détecter. D'où la tolérance bornée.
    """
    if stats.pushed_at is None:
        return None
    delta = now - stats.pushed_at
    if delta < -CLOCK_SKEW_TOLERANCE:
        return None
    return max(0, delta.days)


def star_growth_pct(stats: RepoStats) -> float | None:
    """Croissance relative des étoiles depuis le snapshot précédent.

    ``None`` au premier passage : un delta demande deux observations, et un 0.0
    y affirmerait une stagnation qu'on n'a pas observée.
    """
    if stats.stars is None or stats.stars_prev is None or stats.stars_prev <= 0:
        return None
    return (stats.stars - stats.stars_prev) / stats.stars_prev
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_activity.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/ tests/test_github_activity.py
git commit -m "feat(collector-github): ratios d'activite par depot"
```

---

## Task 3 : Agrégat multi-dépôts

> **Invariant imposé par la tâche 1.** `DeveloperEvent` porte un `model_validator` qui
> **rejette** `repo_count == 0` sans `all_repos_archived=True` — à la construction *et* au
> décodage. La branche « tous les dépôts archivés » de `aggregate()` doit donc impérativement
> poser `all_repos_archived=True` en même temps que `repo_count=0`, sinon l'événement ne se
> construira pas en tâche 8. Les trois sorties de `aggregate()` ci-dessous respectent
> l'invariant par construction ; toute quatrième branche ajoutée devra le vérifier.

**Files:**
- Create: `services/collector-github/app/domain/aggregate.py`
- Test: `tests/test_github_aggregate.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_aggregate.py
from datetime import UTC, datetime

from service_modules import load_service_module

RepoStats = load_service_module("collector-github", "domain.activity").RepoStats
aggregate = load_service_module("collector-github", "domain.aggregate").aggregate

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def _repo(name, **kw):
    base = dict(
        owner="x", repo=name, stars=1000, pushed_at=datetime(2026, 8, 1, tzinfo=UTC),
        archived=False, is_fork=False, commits_4w=40, commits_median_52w=10.0,
        pr_merged_4w=8, pr_merged_52w=104, stars_prev=1000,
    )
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
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_aggregate.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `domain.aggregate` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/domain/aggregate.py` :

```python
"""Agrégation des dépôts d'un même coin. Pur, sans I/O.

Un coin a N dépôts : un client de référence, des bibliothèques, parfois un site.
L'agrégat somme les comptages puis recalcule les ratios sur la somme, plutôt que
de moyenner des ratios — une moyenne de ratios donne le même poids à un dépôt de
documentation qu'au client principal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .activity import RepoStats, commit_ratio, days_since_push, pr_ratio, star_growth_pct


@dataclass(frozen=True, slots=True)
class CoinActivity:
    repo_count: int
    commit_ratio_4w: float | None
    pr_ratio_4w: float | None
    days_since_push: int | None
    star_growth_pct_7d: float | None
    all_repos_archived: bool
    #: Un `pushed_at` existait mais a été rejeté (horloge décalée). N'entre pas
    #: dans l'événement : c'est un signal d'observabilité pour l'appelant, qui
    #: seul a le droit d'incrémenter un compteur.
    push_timestamp_rejected: bool = False


def _is_live(stats: RepoStats) -> bool:
    """Un dépôt archivé ou forké n'est pas une mesure d'activité à zéro.

    Il est hors sujet : un miroir n'a pas vocation à commiter et un dépôt
    archivé annonce lui-même qu'il ne bougera plus. Les compter à zéro
    diluerait l'activité réelle du projet proportionnellement au nombre de
    miroirs qu'il traîne.
    """
    return not stats.archived and not stats.is_fork


def _sum_or_none(values: Sequence[int | None]) -> int | None:
    """Somme, ou None si *aucune* valeur n'a été mesurée.

    Les None individuels (dépôt en 202) sont ignorés plutôt que de contaminer
    tout l'agrégat : deux dépôts mesurés sur trois valent mieux qu'aucune
    lecture. Mais zéro dépôt mesuré ne vaut pas 0.
    """
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def aggregate(repos: Sequence[RepoStats], now: datetime) -> CoinActivity | None:
    """Agrège les dépôts d'un coin, ou ``None`` si aucun dépôt n'est connu.

    La distinction est load-bearing : « aucun dépôt connu » veut dire que le
    mapping n'a rien trouvé, tandis que « tous les dépôts archivés » est un
    constat de mort mesuré. Le premier doit laisser l'axe absent, le second
    doit le noter à zéro.
    """
    if not repos:
        return None

    live = [r for r in repos if _is_live(r)]
    if not live:
        return CoinActivity(
            repo_count=0,
            commit_ratio_4w=0.0,
            pr_ratio_4w=0.0,
            days_since_push=None,
            star_growth_pct_7d=None,
            all_repos_archived=True,
        )

    # ATTENTION — décision à prendre ici, pas ailleurs. `star_growth_pct` divise
    # par `stars_at - stars_prev_at`, et chaque dépôt a *ses* deux instants : le
    # round-robin ne les rafraîchit pas ensemble. Sommer les étoiles puis
    # diviser par un intervalle inventé pour l'ensemble redonnerait exactement
    # le défaut que la tâche 2 a corrigé.
    #
    # Recommandation : calculer le taux **par dépôt**, puis en faire une moyenne
    # pondérée par `stars_prev`. C'est identique à la somme quand les intervalles
    # coïncident, et correct quand ils divergent. Ne pas prendre `min`/`max` des
    # instants : la fenêtre la plus étroite surestime le taux, donc biaise à la
    # hausse — le mauvais sens.
    merged = RepoStats(
        owner=live[0].owner,
        repo=f"<{len(live)} repos>",
        stars=_sum_or_none([r.stars for r in live]),
        pushed_at=max((r.pushed_at for r in live if r.pushed_at), default=None),
        commits_4w=_sum_or_none([r.commits_4w for r in live]),
        commits_median_52w=_sum_median([r.commits_median_52w for r in live]),
        pr_merged_4w=_sum_or_none([r.pr_merged_4w for r in live]),
        pr_merged_52w=_sum_or_none([r.pr_merged_52w for r in live]),
        stars_prev=_sum_or_none([r.stars_prev for r in live]),
    )
    freshness = days_since_push(merged, now)
    return CoinActivity(
        repo_count=len(live),
        commit_ratio_4w=commit_ratio(merged),
        pr_ratio_4w=pr_ratio(merged),
        days_since_push=freshness,
        # Plus de `now` : l'intervalle court d'un snapshot à l'autre, pas
        # jusqu'à l'horloge du cycle. Voir la note ci-dessus sur l'agrégation.
        star_growth_pct_7d=_weighted_star_growth(live),
        all_repos_archived=False,
        # Rapporté, pas compté : ce module reste pur, et c'est l'appelant qui
        # incrémente la métrique. L'horodatage existait mais n'a pas été cru —
        # horloge décalée au-delà de CLOCK_SKEW_TOLERANCE. Sans cette
        # remontée la perte serait invisible : freshness pèse 0.25 de l'axe,
        # et une dérive d'horloge l'annule pour *tous* les dépôts récemment
        # poussés d'un coup, l'axe se renormalisant en silence sur 0.75.
        push_timestamp_rejected=merged.pushed_at is not None and freshness is None,
    )


def _weighted_star_growth(live: Sequence[RepoStats]) -> float | None:
    """Taux de croissance d'étoiles du coin, pondéré par la base de chaque dépôt.

    Par dépôt puis moyenné, jamais sommé : chaque dépôt porte ses propres
    ``stars_at``/``stars_prev_at``, et le round-robin ne les rafraîchit pas
    ensemble. Diviser une somme d'étoiles par un intervalle commun inventé
    rejouerait le défaut corrigé en tâche 2, où l'intervalle réel et
    l'intervalle supposé divergeaient.

    Pondéré par ``stars_prev`` parce qu'un dépôt de documentation à 30 étoiles
    ne doit pas peser autant que le client principal à 40 000.
    """
    rates = [
        (star_growth_pct(r), r.stars_prev)
        for r in live
        if r.stars_prev is not None and r.stars_prev > 0
    ]
    usable = [(rate, base) for rate, base in rates if rate is not None]
    if not usable:
        return None
    total = sum(base for _, base in usable)
    return sum(rate * base for rate, base in usable) / total


def _sum_median(values: Sequence[float | None]) -> float | None:
    """Somme des médianes hebdomadaires.

    Ce n'est pas la médiane de la somme, et c'est volontaire : on ne dispose
    que des médianes par dépôt, et leur somme est la meilleure estimation du
    rythme de croisière de l'ensemble sans redemander les 52 séries.
    """
    present = [v for v in values if v is not None]
    return sum(present) if present else None
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_aggregate.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/app/domain/aggregate.py tests/test_github_aggregate.py
git commit -m "feat(collector-github): agregat multi-depots par coin"
```

---

## Task 4 : Parsing des deux README

**Files:**
- Create: `services/collector-github/app/domain/lists.py`
- Create: `tests/fixtures/best_of_crypto_sample.md`, `tests/fixtures/awesome_crypto_sample.md`
- Test: `tests/test_github_lists.py`

- [ ] **Step 1 : écrire les fixtures et le test qui échoue**

`tests/fixtures/best_of_crypto_sample.md` :

```markdown
## Cryptocurrencies

<details><summary><b><a href="https://github.com/bitcoin/bitcoin">bitcoin</a></b> (🥇36 ·  ⭐ 77K) - Bitcoin Core integration/staging tree. <code><a href="http://bit.ly/34MBwT8">MIT</a></code></summary>

- [GitHub](https://github.com/bitcoin/bitcoin) (👨‍💻 1.2K · 🔀 35K · 📦 21 · 📋 8.3K - 8% open · ⏱️ 05.06.2024):
</details>

<details><summary><b><a href="https://github.com/ethereum/go-ethereum">go-ethereum</a></b> (🥇38 ·  ⭐ 47K) - Go implementation of the Ethereum protocol.</summary>

- [GitHub](https://github.com/ethereum/go-ethereum) (👨‍💻 900):
</details>

<details><summary><b>broken-entry-without-link</b> (⭐ 1)</summary></details>
```

`tests/fixtures/awesome_crypto_sample.md` :

```markdown
### [bitcoin](https://github.com/bitcoin/bitcoin)  
Bitcoin Core integration/staging tree  
[https://bitcoincore.org/en/download](https://bitcoincore.org/en/download)  
[https://github.com/bitcoin/bitcoin](https://github.com/bitcoin/bitcoin)  
111 stars per week over 773 weeks  
86,064 stars, 38,014 forks, 4,056 watches  
<sub><sup>bitcoin, cryptocurrency, p2p</sup></sub>

### [solana](https://github.com/solana-labs/solana)  
Web-Scale Blockchain for fast, secure, scalable, decentralized apps  
[https://github.com/solana-labs/solana](https://github.com/solana-labs/solana)  
200 stars per week over 300 weeks  
<sub><sup>blockchain, solana</sup></sub>
```

```python
# tests/test_github_lists.py
from pathlib import Path

from service_modules import load_service_module

_lists = load_service_module("collector-github", "domain.lists")
ListEntry = _lists.ListEntry
parse_awesome_crypto = _lists.parse_awesome_crypto
parse_best_of_crypto = _lists.parse_best_of_crypto

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_best_of_extracts_repos():
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    by_repo = {(e.owner, e.repo): e for e in entries}
    assert ("bitcoin", "bitcoin") in by_repo
    assert ("ethereum", "go-ethereum") in by_repo


def test_best_of_has_no_homepage():
    """La liste n'en publie pas. None, pas une URL devinee depuis le repo."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert all(e.homepage_url is None for e in entries)


def test_best_of_deduplicates_summary_and_body_links():
    """Chaque entree porte deux fois la meme URL; une seule doit sortir."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert len(entries) == 2


def test_awesome_extracts_homepage_when_present():
    entries = {e.repo: e for e in parse_awesome_crypto(_read("awesome_crypto_sample.md"))}
    assert entries["bitcoin"].homepage_url == "https://bitcoincore.org/en/download"


def test_awesome_homepage_is_none_when_only_github_line():
    """La ligne site est absente pour solana: None, pas l'URL GitHub recopiee."""
    entries = {e.repo: e for e in parse_awesome_crypto(_read("awesome_crypto_sample.md"))}
    assert entries["solana"].homepage_url is None
    assert entries["solana"].owner == "solana-labs"


def test_unparseable_entries_are_counted_not_guessed():
    """Un format qui change doit se voir, pas retrecir le registre en silence."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert all(isinstance(e, ListEntry) for e in entries)
    # l'entree sans lien n'est pas inventee
    assert not any(e.name == "broken-entry-without-link" for e in entries)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_lists.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `domain.lists` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/domain/lists.py` :

```python
"""Parsing des deux listes de référence. Pur, sans I/O.

Les deux README ne publient pas la même chose, et le registre le reflète tel
quel : ``best-of-crypto`` n'expose qu'un lien GitHub, ``awesome-crypto`` publie
en plus l'URL du site officiel sur une ligne dédiée. Déduire une homepage depuis
un lien GitHub (``owner.github.io``) fabriquerait une URL que personne n'a
publiée, donc ``homepage_url`` reste ``None`` pour toute la première liste.

Le parsing est tolérant mais jamais devinant : une entrée dont le lien ne se
laisse pas lire est comptée et ignorée. Le compteur remonte en métrique, parce
qu'un changement de format doit se voir plutôt que faire rétrécir le registre
sans bruit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: https://github.com/<owner>/<repo>, sans capturer les sous-chemins
#: (/issues, /blob/...) ni le .git final.
_REPO_URL = re.compile(
    r"https?://github\.com/([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?(?=[/\s)\"'#]|$)"
)
#: `### [nom](url)` en tête d'entrée pour awesome-crypto.
_AWESOME_HEAD = re.compile(r"^###\s+\[([^\]]+)\]\((https?://[^)]+)\)", re.MULTILINE)
#: `[url](url)` sur une ligne isolée.
_BARE_LINK = re.compile(r"^\s*\[(https?://[^\]]+)\]\((https?://[^)]+)\)\s*$")
#: <a href="...">nom</a> dans un <summary> best-of.
_SUMMARY = re.compile(
    r"<summary>.*?<a href=\"(https?://github\.com/[^\"]+)\"[^>]*>\s*([^<]+?)\s*</a>",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ListEntry:
    name: str
    owner: str
    repo: str
    homepage_url: str | None = None
    description: str | None = None
    source_list: str = ""

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class ParseResult:
    entries: list[ListEntry]
    #: Blocs reconnus comme des entrées mais dont le lien n'a pas pu être lu.
    unparsed: int


def parse_best_of_crypto(markdown: str) -> list[ListEntry]:
    """Une entrée par bloc ``<details>``, le lien étant celui du ``<summary>``.

    Le corps du bloc répète la même URL sous ``- [GitHub](...)`` ; ne lire que
    le ``<summary>`` évite de compter chaque projet deux fois.
    """
    return parse_best_of_crypto_full(markdown).entries


def parse_best_of_crypto_full(markdown: str) -> ParseResult:
    entries: list[ListEntry] = []
    unparsed = 0
    seen: set[tuple[str, str]] = set()
    for block in markdown.split("<details>"):
        if "<summary>" not in block:
            continue
        match = _SUMMARY.search(block)
        if match is None:
            unparsed += 1
            continue
        repo_match = _REPO_URL.match(match.group(1))
        if repo_match is None:
            unparsed += 1
            continue
        owner, repo = repo_match.group(1), repo_match.group(2)
        if (owner, repo) in seen:
            continue
        seen.add((owner, repo))
        entries.append(
            ListEntry(
                name=match.group(2).strip(),
                owner=owner,
                repo=repo,
                homepage_url=None,  # la liste n'en publie pas
                source_list="best-of-crypto",
            )
        )
    return ParseResult(entries=entries, unparsed=unparsed)


def parse_awesome_crypto(markdown: str) -> list[ListEntry]:
    return parse_awesome_crypto_full(markdown).entries


def parse_awesome_crypto_full(markdown: str) -> ParseResult:
    """Une entrée par titre ``###``; la homepage est la première ligne-lien
    isolée qui ne pointe pas vers GitHub.

    L'ordre des lignes n'est pas garanti et la ligne site est parfois absente
    (cf. solana dans les fixtures) : chercher « la ligne qui n'est pas GitHub »
    est plus robuste que compter les lignes.
    """
    entries: list[ListEntry] = []
    unparsed = 0
    heads = list(_AWESOME_HEAD.finditer(markdown))
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(markdown)
        body = markdown[head.end() : end]
        repo_match = _REPO_URL.match(head.group(2))
        if repo_match is None:
            unparsed += 1
            continue
        homepage: str | None = None
        description: str | None = None
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            link = _BARE_LINK.match(line)
            if link is not None:
                if homepage is None and "github.com" not in link.group(2):
                    homepage = link.group(2)
                continue
            if description is None and not stripped.startswith("<"):
                description = stripped
        entries.append(
            ListEntry(
                name=head.group(1).strip(),
                owner=repo_match.group(1),
                repo=repo_match.group(2),
                homepage_url=homepage,
                description=description,
                source_list="awesome-crypto",
            )
        )
    return ParseResult(entries=entries, unparsed=unparsed)
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_lists.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/app/domain/lists.py tests/test_github_lists.py tests/fixtures/
git commit -m "feat(collector-github): parsing des deux awesome-lists"
```

---

## Task 5 : Client GitHub

**Files:**
- Create: `services/collector-github/app/infrastructure/__init__.py`, `github_client.py`
- Test: `tests/test_github_client.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_client.py
import json

import httpx
import pytest

from service_modules import load_service_module

_gh = load_service_module("collector-github", "infrastructure.github_client")
GitHubClient = _gh.GitHubClient
RepoGone = _gh.RepoGone


def _client(handler):
    return GitHubClient(token="t", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_commit_activity_returns_none_while_github_computes():
    """202 = statistiques en cours de calcul. None, jamais une liste vide."""

    def handler(request):
        return httpx.Response(202, content=b"")

    result = await _client(handler).commit_activity("a", "b")
    assert result is None


@pytest.mark.asyncio
async def test_commit_activity_returns_weekly_totals():
    weeks = [{"total": n, "week": 0, "days": []} for n in range(52)]

    def handler(request):
        return httpx.Response(200, json=weeks)

    result = await _client(handler).commit_activity("a", "b")
    assert result is not None
    assert len(result) == 52
    assert result[-1] == 51


@pytest.mark.asyncio
async def test_empty_list_is_none_not_zero_activity():
    """GitHub renvoie [] pour un depot vide *et* parfois pendant le calcul."""

    def handler(request):
        return httpx.Response(200, json=[])

    assert await _client(handler).commit_activity("a", "b") is None


@pytest.mark.asyncio
async def test_404_raises_repo_gone():
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(RepoGone):
        await _client(handler).repo("a", "b")


@pytest.mark.asyncio
async def test_repo_reads_metadata():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "stargazers_count": 100, "forks_count": 5,
                "pushed_at": "2026-08-01T00:00:00Z",
                "archived": False, "fork": False,
            },
        )

    meta = await _client(handler).repo("a", "b")
    assert meta.stars == 100
    assert meta.archived is False
    assert meta.pushed_at.year == 2026


@pytest.mark.asyncio
async def test_merged_pr_count_uses_search():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"total_count": 42})

    count = await _client(handler).merged_pr_count("a", "b", since_days=28)
    assert count == 42
    assert "is:merged" in seen["url"]


@pytest.mark.asyncio
async def test_search_and_core_quotas_are_tracked_separately():
    """L'API /search a son propre seau: 30/min, distinct des 5000/h du coeur."""
    client = _client(lambda r: httpx.Response(200, json={"total_count": 1}))
    assert client.search_limiter is not client.core_limiter
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_client.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `infrastructure.github_client` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/infrastructure/__init__.py` : fichier vide.

`services/collector-github/app/infrastructure/github_client.py` :

```python
"""Accès REST à GitHub, avec les deux quotas et les trois échecs silencieux.

Trois comportements de l'API coûtent cher si on ne les traite pas nommément :

* **202 sur /stats/**\\ *. GitHub calcule ces séries de façon asynchrone et
  répond 202 avec un corps vide en attendant. Rendre ``[]`` ou ``0`` ici
  affirmerait « ce dépôt n'a pas commité de l'année », ce qui est une tout
  autre déclaration que « GitHub n'a pas fini de compter ». On rend ``None`` et
  le dépôt repasse au tour suivant.
* **404.** Dépôt renommé, supprimé ou passé privé. Levé comme ``RepoGone`` pour
  que l'appelant l'écrive au tableau noir plutôt que de redemander chaque cycle.
* **Deux seaux de quota distincts.** Le cœur REST accorde 5 000 requêtes/heure,
  l'API ``/search`` 30 par minute. Les mélanger fait passer le service pour dans
  les clous jusqu'au premier 403 de rate-limit secondaire.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from cmi_common.observability import UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)

SERVICE = "collector-github"
API_BASE = "https://api.github.com"
#: Le cœur REST : 5 000 req/h pour un compte authentifié.
CORE_PER_MIN = 80
#: L'API de recherche : 30 req/min, seau séparé.
SEARCH_PER_MIN = 25


class RepoGone(Exception):
    """Le dépôt n'existe plus sous ce nom pour ce token."""


@dataclass(frozen=True, slots=True)
class RepoMeta:
    stars: int | None
    forks: int | None
    pushed_at: datetime | None
    archived: bool
    is_fork: bool


class _Limiter:
    """Fenêtre glissante minute, ajustée par les en-têtes de l'API."""

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / max(1, per_minute)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._next - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next = loop.time() + self._interval

    def observe(self, response: httpx.Response) -> None:
        """Se recale sur ce que l'API annonce plutôt que sur une constante.

        ``x-ratelimit-remaining`` proche de zéro veut dire qu'on a mal estimé le
        coût des appels : on étale le reste de la fenêtre sur le temps qui reste
        au lieu d'attendre le 403.
        """
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining is None or reset is None:
            return
        try:
            left, reset_at = int(remaining), int(reset)
        except ValueError:
            return
        if left <= 0:
            seconds = max(0.0, reset_at - datetime.now(tz=UTC).timestamp())
            self._next = asyncio.get_running_loop().time() + seconds
            UPSTREAM_REQUESTS.labels(SERVICE, "github", "throttled").inc()


class GitHubClient:
    def __init__(
        self,
        *,
        token: str,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.core_limiter = _Limiter(CORE_PER_MIN)
        self.search_limiter = _Limiter(SEARCH_PER_MIN)
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, limiter: _Limiter, **params: str) -> httpx.Response:
        await limiter.acquire()
        response = await self._http.get(path, params=params or None)
        limiter.observe(response)
        if response.status_code == 404:
            UPSTREAM_REQUESTS.labels(SERVICE, "github", "gone").inc()
            raise RepoGone(path)
        if response.status_code >= 400 and response.status_code != 202:
            UPSTREAM_REQUESTS.labels(SERVICE, "github", "error").inc()
            response.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "github", "ok").inc()
        return response

    async def repo(self, owner: str, repo: str) -> RepoMeta:
        data = (await self._get(f"/repos/{owner}/{repo}", self.core_limiter)).json()
        pushed = data.get("pushed_at")
        return RepoMeta(
            stars=data.get("stargazers_count"),
            forks=data.get("forks_count"),
            pushed_at=(
                datetime.fromisoformat(pushed.replace("Z", "+00:00")) if pushed else None
            ),
            archived=bool(data.get("archived")),
            is_fork=bool(data.get("fork")),
        )

    async def commit_activity(self, owner: str, repo: str) -> list[int] | None:
        """52 totaux hebdomadaires, ou ``None`` si GitHub calcule encore.

        Une liste vide est traitée comme ``None`` et non comme « 52 semaines à
        zéro » : l'API la renvoie aussi bien pour un dépôt réellement vide que
        pendant le calcul, et les deux ne se distinguent pas au moment de la
        lecture. Rendre ``None`` coûte un cycle ; rendre ``0`` fabriquerait une
        année de silence.
        """
        response = await self._get(
            f"/repos/{owner}/{repo}/stats/commit_activity", self.core_limiter
        )
        if response.status_code == 202:
            return None
        weeks = response.json()
        if not isinstance(weeks, list) or not weeks:
            return None
        return [int(week.get("total", 0)) for week in weeks]

    async def merged_pr_count(self, owner: str, repo: str, *, since_days: int) -> int | None:
        since = (datetime.now(tz=UTC) - timedelta(days=since_days)).date().isoformat()
        response = await self._get(
            "/search/issues",
            self.search_limiter,
            q=f"repo:{owner}/{repo} is:pr is:merged merged:>{since}",
            per_page="1",
        )
        total = response.json().get("total_count")
        return int(total) if total is not None else None


def weekly_median(weeks: list[int] | None) -> float | None:
    """Médiane hebdomadaire sur les 52 semaines, hors 4 dernières.

    Les 4 dernières semaines sont exclues parce qu'elles constituent la fenêtre
    *récente* : les inclure dans leur propre baseline amortit exactement le
    mouvement qu'on cherche à détecter.
    """
    if weeks is None or len(weeks) < 8:
        return None
    return float(statistics.median(weeks[:-4]))


def recent_commits(weeks: list[int] | None) -> int | None:
    if weeks is None or len(weeks) < 4:
        return None
    return sum(weeks[-4:])
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_client.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/app/infrastructure/ tests/test_github_client.py
git commit -m "feat(collector-github): client REST, 202/404 et deux seaux de quota"
```

---

## Task 6 : Tables et migration

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py`
- Create: `migrations/alembic/versions/0017_github_activity.py`
- Test: `tests/test_github_models.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_models.py
from cmi_common.db.models import Base, CoinRepoMap, CryptoProjectRegistry, GithubRepoSnapshot


def test_tables_are_registered():
    names = set(Base.metadata.tables)
    assert {"crypto_project_registry", "coin_repo_map", "github_repo_snapshot"} <= names


def test_registry_symbol_is_nullable():
    """Un projet sans ticker resolu reste dans le registre, symbol NULL."""
    assert CryptoProjectRegistry.__table__.c.symbol.nullable is True


def test_registry_homepage_is_nullable():
    """best-of-crypto ne publie pas d'URL de site: NULL, pas une chaine vide."""
    assert CryptoProjectRegistry.__table__.c.homepage_url.nullable is True


def test_snapshot_measures_are_all_nullable():
    """Chaque mesure peut manquer (202, depot neuf). Aucune ne vaut 0 par defaut."""
    cols = GithubRepoSnapshot.__table__.c
    for name in ("stars", "commits_4w", "commits_median_52w", "pr_merged_4w", "pr_merged_52w"):
        assert cols[name].nullable is True, name
        assert cols[name].default is None, name


def test_repo_map_is_unique_per_coin_and_repo():
    pk = {c.name for c in CoinRepoMap.__table__.primary_key}
    assert pk == {"coin_id", "owner", "repo"}
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'CoinRepoMap'`

- [ ] **Step 3 : implémenter**

Dans `libs/cmi_common/cmi_common/db/models.py`, après `VenuePair` :

```python
class CryptoProjectRegistry(Base):
    """Projets crypto recensés par les deux listes de référence.

    Un projet sans ticker résolu garde ``symbol = NULL`` et reste ici : le
    registre est un catalogue, pas la table de scoring. ``NULL`` veut dire « pas
    de rattachement connu », ce qui n'affirme rien sur l'activité du projet.

    ``homepage_url`` est ``NULL`` pour tout ce qui vient de best-of-crypto, qui
    n'en publie pas. La déduire du lien GitHub inventerait une URL.
    """

    __tablename__ = "crypto_project_registry"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_url: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    homepage_url: Mapped[str | None] = mapped_column(String(512), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    category: Mapped[str | None] = mapped_column(String(64), default=None)
    source_list: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CoinRepoMap(Base):
    """Rattachement de confiance ``coin -> dépôt``.

    ``origin`` distingue le mapping officiel CoinGecko (``links.repos_url.github``)
    de la promotion depuis une awesome-list. Les deux ne se valent pas et un
    doute futur sur la qualité de l'axe se tranchera en filtrant là-dessus.
    """

    __tablename__ = "coin_repo_map"

    coin_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), primary_key=True)
    repo: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    origin: Mapped[str] = mapped_column(String(16))
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GithubRepoSnapshot(Base):
    """Une lecture d'un dépôt à un instant donné.

    Existe pour une seule raison : les deltas. GitHub ne publie pas d'historique
    d'étoiles exploitable, donc la croissance se calcule entre deux snapshots —
    et vaut ``None`` tant qu'il n'y en a qu'un.
    """

    __tablename__ = "github_repo_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(128))
    repo: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    stars: Mapped[int | None] = mapped_column(Integer, default=None)
    forks: Mapped[int | None] = mapped_column(Integer, default=None)
    commits_4w: Mapped[int | None] = mapped_column(Integer, default=None)
    commits_median_52w: Mapped[float | None] = mapped_column(Float, default=None)
    pr_merged_4w: Mapped[int | None] = mapped_column(Integer, default=None)
    pr_merged_52w: Mapped[int | None] = mapped_column(Integer, default=None)
    pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fork: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_github_snapshot_repo", "owner", "repo", "observed_at"),)
```

Vérifier que `Float`, `Integer`, `Text` et `Index` sont bien importés en tête du fichier ; les ajouter à l'import `from sqlalchemy import ...` sinon.

`migrations/alembic/versions/0017_github_activity.py` :

```python
"""Registre des projets crypto, mapping coin->repo, snapshots GitHub

Trois tables pour l'axe developer_activity. Le registre est volontairement
découplé du mapping : il recense ~8 400 projets issus des deux awesome-lists,
dont la grande majorité n'a pas de ticker, tandis que coin_repo_map ne contient
que les rattachements de confiance effectivement scorés.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crypto_project_registry",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("github_url", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("homepage_url", sa.String(512)),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.String(64)),
        sa.Column("source_list", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_registry_symbol", "crypto_project_registry", ["symbol"])

    op.create_table(
        "coin_repo_map",
        sa.Column("coin_id", sa.String(128), primary_key=True),
        sa.Column("owner", sa.String(128), primary_key=True),
        sa.Column("repo", sa.String(128), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_repo_map_symbol", "coin_repo_map", ["symbol"])

    op.create_table(
        "github_repo_snapshot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("repo", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stars", sa.Integer),
        sa.Column("forks", sa.Integer),
        sa.Column("commits_4w", sa.Integer),
        sa.Column("commits_median_52w", sa.Float),
        sa.Column("pr_merged_4w", sa.Integer),
        sa.Column("pr_merged_52w", sa.Integer),
        sa.Column("pushed_at", sa.DateTime(timezone=True)),
        sa.Column("archived", sa.Boolean, server_default=sa.false()),
        sa.Column("is_fork", sa.Boolean, server_default=sa.false()),
    )
    op.create_index(
        "ix_github_snapshot_repo", "github_repo_snapshot", ["owner", "repo", "observed_at"]
    )


def downgrade() -> None:
    op.drop_table("github_repo_snapshot")
    op.drop_table("coin_repo_map")
    op.drop_table("crypto_project_registry")
```

- [ ] **Step 4 : lancer le test et la migration**

Run: `python -m pytest tests/test_github_models.py -v`
Expected: PASS, 5 tests

Run: `make migrate`
Expected: `Running upgrade 0016 -> 0017`

- [ ] **Step 5 : commit**

```bash
git add libs/cmi_common/cmi_common/db/models.py migrations/alembic/versions/0017_github_activity.py tests/test_github_models.py
git commit -m "feat(db): registre projets, mapping coin-repo et snapshots GitHub"
```

---

## Task 7 : Mapping `coin → repos`

**Files:**
- Create: `services/collector-github/app/infrastructure/repo_map.py`
- Test: `tests/test_github_repo_map.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_repo_map.py
import httpx
import pytest

from service_modules import load_service_module

ListEntry = load_service_module("collector-github", "domain.lists").ListEntry
_map = load_service_module("collector-github", "infrastructure.repo_map")
CoinGeckoRepos = _map.CoinGeckoRepos
promote_list_entries = _map.promote_list_entries


def _client(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    return CoinGeckoRepos(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_reads_github_repos_from_links():
    repos = await _client(
        {"links": {"repos_url": {"github": ["https://github.com/aave/aave-v3-core"]}}}
    ).repos_for("aave")
    assert repos == [("aave", "aave-v3-core")]


@pytest.mark.asyncio
async def test_absent_links_return_empty_not_none():
    """CoinGecko connait le coin mais ne liste aucun repo: une reponse, pas un echec."""
    assert await _client({"links": {}}).repos_for("some-coin") == []


@pytest.mark.asyncio
async def test_non_github_urls_are_ignored():
    repos = await _client(
        {"links": {"repos_url": {"github": ["https://gitlab.com/x/y"], "bitbucket": []}}}
    ).repos_for("x")
    assert repos == []


def test_promotion_requires_unambiguous_lexicon_match():
    entries = [
        ListEntry(name="aave", owner="aave", repo="aave-v3-core", source_list="l"),
        ListEntry(name="ccxt", owner="ccxt", repo="ccxt", source_list="l"),
    ]
    promoted = promote_list_entries(
        entries, symbols_by_name={"aave": "AAVE"}, homographs=frozenset()
    )
    assert promoted == [("AAVE", "aave", "aave-v3-core")]


def test_homographs_are_never_promoted():
    """KEEP, FLOW, ONE: le lexicon les signale, cette voie ne les tranche pas."""
    entries = [ListEntry(name="keep", owner="keep-network", repo="keep-core", source_list="l")]
    promoted = promote_list_entries(
        entries, symbols_by_name={"keep": "KEEP"}, homographs=frozenset({"KEEP"})
    )
    assert promoted == []
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_repo_map.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `infrastructure.repo_map` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/infrastructure/repo_map.py` :

```python
"""Rattachement ``coin -> dépôts``, colonne vertébrale du signal.

CoinGecko fait autorité : ``/coins/{id}`` publie ``links.repos_url.github``,
c'est-à-dire les dépôts que le projet lui-même déclare. Les awesome-lists ne
servent qu'à proposer des candidats supplémentaires, et un candidat n'est promu
que si son nom résout sans ambiguïté contre le lexicon.

Le filtre est délibérément sévère. Sur ~8 400 entrées cumulées, la grande
majorité sont des bibliothèques sans token (ccxt, web3.py, OpenZeppelin) : une
promotion trop généreuse rattacherait l'activité d'un outil générique à un coin
au hasard, ce qui produirait une mesure — donc un déplacement du score — à
partir d'une coïncidence de nommage.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

import httpx

from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.lists import ListEntry

logger = logging.getLogger(__name__)

SERVICE = "collector-github"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoRepos:
    """Lit ``links.repos_url.github`` pour un coin.

    Ce mapping change au rythme des listings, pas des polls : l'appelant le met
    en cache 30 jours et ne rafraîchit que quelques coins par cycle, ce qui tient
    dans le quota gratuit (10 à 30 appels/minute).
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=COINGECKO_BASE, timeout=timeout, transport=transport
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def repos_for(self, coin_id: str) -> list[tuple[str, str]]:
        """``[(owner, repo), ...]``, liste vide si le coin n'en déclare aucun.

        Une liste vide est une réponse — « CoinGecko connaît ce coin et il ne
        publie pas de dépôt » — et non un échec. L'appelant la distingue d'une
        exception, qui elle veut dire « on n'a pas pu demander ».
        """
        response = await self._http.get(
            f"/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            },
        )
        if response.status_code >= 400:
            UPSTREAM_REQUESTS.labels(SERVICE, "coingecko", "error").inc()
            response.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "coingecko", "ok").inc()
        urls = (
            response.json().get("links", {}).get("repos_url", {}).get("github") or []
        )
        return [pair for pair in (_split(url) for url in urls) if pair is not None]


def _split(url: str) -> tuple[str, str] | None:
    marker = "github.com/"
    if marker not in url:
        return None
    parts = url.split(marker, 1)[1].strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1].removesuffix(".git")


def promote_list_entries(
    entries: Iterable[ListEntry],
    *,
    symbols_by_name: Mapping[str, str],
    homographs: frozenset[str],
) -> list[tuple[str, str, str]]:
    """``[(symbol, owner, repo), ...]`` pour les seules entrées non ambiguës.

    Deux garde-fous, dans cet ordre : le nom du projet doit résoudre contre le
    lexicon, et le symbole obtenu ne doit pas figurer parmi les homographes que
    le lexicon signale déjà (ONE, KEEP, FLOW…). Ces derniers demandent une
    corroboration que le seul nom d'un dépôt ne fournit pas.
    """
    promoted: list[tuple[str, str, str]] = []
    for entry in entries:
        symbol = symbols_by_name.get(entry.name.strip().lower())
        if symbol is None or symbol in homographs:
            continue
        promoted.append((symbol, entry.owner, entry.repo))
    return promoted
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_repo_map.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/app/infrastructure/repo_map.py tests/test_github_repo_map.py
git commit -m "feat(collector-github): mapping coin-repo ancre sur CoinGecko"
```

---

## Task 8 : Le cycle deux horloges

**Files:**
- Create: `services/collector-github/app/application/__init__.py`, `collector.py`
- Test: `tests/test_github_collector.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_collector.py
from datetime import UTC, datetime

import pytest

from service_modules import load_service_module

GitHubCollector = load_service_module(
    "collector-github", "application.collector"
).GitHubCollector
RepoStats = load_service_module("collector-github", "domain.activity").RepoStats

NOW = datetime(2026, 8, 2, tzinfo=UTC)


class _Producer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append(event)


class _Store:
    """Cache de snapshots en memoire, indexe par (owner, repo)."""

    def __init__(self, seeded=None):
        self.rows = dict(seeded or {})

    async def latest(self, owner, repo):
        return self.rows.get((owner, repo))

    async def save(self, stats):
        self.rows[(stats.owner, stats.repo)] = stats


def _stats(owner="aave", repo="core", **kw):
    base = dict(
        stars=1000, pushed_at=NOW, archived=False, is_fork=False,
        commits_4w=40, commits_median_52w=10.0, pr_merged_4w=8, pr_merged_52w=104,
        stars_prev=1000,
    )
    base.update(kw)
    return RepoStats(owner=owner, repo=repo, **base)


def _collector(producer, store, fetched=None, **kw):
    async def fetch(owner, repo):
        return (fetched or {}).get((owner, repo)) or _stats(owner, repo)

    return GitHubCollector(
        producer=producer,
        store=store,
        fetch_repo=fetch,
        repo_map=lambda: {"AAVE": ("aave", [("aave", "core")])},
        clock=lambda: NOW,
        **kw,
    )


@pytest.mark.asyncio
async def test_publishes_from_cache_every_cycle_even_without_refresh():
    """Le FeatureStore expire a 900s: republier a chaque cycle, ou l'axe disparait."""
    producer = _Producer()
    store = _Store({("aave", "core"): _stats()})
    collector = _collector(producer, store, max_refresh_per_cycle=0)
    await collector.poll_once()
    assert len(producer.published) == 1
    await collector.poll_once()
    assert len(producer.published) == 2


@pytest.mark.asyncio
async def test_refresh_budget_bounds_fetches_not_reports():
    """Budget a 1 mais 3 depots en cache: 3 evenements, 1 seul appel reseau."""
    calls = []

    async def fetch(owner, repo):
        calls.append((owner, repo))
        return _stats(owner, repo)

    producer = _Producer()
    store = _Store({("o", f"r{i}"): _stats("o", f"r{i}") for i in range(3)})
    collector = GitHubCollector(
        producer=producer,
        store=store,
        fetch_repo=fetch,
        repo_map=lambda: {f"S{i}": (f"c{i}", [("o", f"r{i}")]) for i in range(3)},
        clock=lambda: NOW,
        max_refresh_per_cycle=1,
    )
    await collector.poll_once()
    assert len(producer.published) == 3
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_symbol_without_cached_snapshot_publishes_nothing():
    """Rien de mesure != tout a zero. Pas d'evenement plutot qu'un evenement vide."""
    producer = _Producer()
    collector = _collector(producer, _Store(), max_refresh_per_cycle=0)
    await collector.poll_once()
    assert producer.published == []


@pytest.mark.asyncio
async def test_refresh_cursor_rotates():
    """Sans rotation, les premiers depots affameraient les suivants."""
    calls = []

    async def fetch(owner, repo):
        calls.append(repo)
        return _stats(owner, repo)

    store = _Store({("o", f"r{i}"): _stats("o", f"r{i}") for i in range(3)})
    collector = GitHubCollector(
        producer=_Producer(),
        store=store,
        fetch_repo=fetch,
        repo_map=lambda: {f"S{i}": (f"c{i}", [("o", f"r{i}")]) for i in range(3)},
        clock=lambda: NOW,
        max_refresh_per_cycle=1,
    )
    await collector.poll_once()
    await collector.poll_once()
    await collector.poll_once()
    assert sorted(calls) == ["r0", "r1", "r2"]


@pytest.mark.asyncio
async def test_one_invalid_token_does_not_silence_the_others():
    """Une ValidationError sur un token ne doit pas couter le cycle aux autres.

    Lecon de l'incident fees_24h_usd: une exception dans une boucle d'emission
    sans garde fait publier zero evenement pour *tous* les tokens du cycle.
    """
    async def fetch(owner, repo):
        return _stats(owner, repo)

    producer = _Producer()
    store = _Store({("o", f"r{i}"): _stats("o", f"r{i}") for i in range(3)})
    collector = GitHubCollector(
        producer=producer,
        store=store,
        fetch_repo=fetch,
        # coin_id None sur le premier: DeveloperEvent.coin_id est un str requis,
        # donc sa construction leve. Une ligne de mapping incomplete est le cas
        # reel le plus proche.
        repo_map=lambda: {
            "S0": (None, [("o", "r0")]),
            "S1": ("c1", [("o", "r1")]),
            "S2": ("c2", [("o", "r2")]),
        },
        clock=lambda: NOW,
        max_refresh_per_cycle=0,
    )
    await collector.poll_once()
    assert {e.symbol for e in producer.published} == {"S1", "S2"}


@pytest.mark.asyncio
async def test_dead_repo_is_written_off_after_one_failure():
    RepoGone = load_service_module(
        "collector-github", "infrastructure.github_client"
    ).RepoGone

    calls = []

    async def fetch(owner, repo):
        calls.append(repo)
        raise RepoGone(repo)

    collector = GitHubCollector(
        producer=_Producer(),
        store=_Store(),
        fetch_repo=fetch,
        repo_map=lambda: {"S": ("c", [("o", "gone")])},
        clock=lambda: NOW,
        max_refresh_per_cycle=5,
    )
    await collector.poll_once()
    await collector.poll_once()
    assert calls == ["gone"]
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_collector.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `application.collector` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/application/__init__.py` : fichier vide.

`services/collector-github/app/application/collector.py` :

```python
"""Un cycle du collector GitHub — deux horloges dans une seule boucle.

Le service tourne toutes les 600 s parce que le ``FeatureStore`` de
``ai-worker-haiku`` expire à 900 s : un axe publié moins souvent que ça est
absent la plupart du temps. Mais l'API GitHub n'a pas besoin d'être interrogée
à ce rythme — l'activité de développement bouge à l'échelle de la semaine.

D'où la dissociation : **publication depuis le cache à chaque cycle**,
**rafraîchissement réseau en round-robin** borné par un budget. Le budget borne
les téléchargements, jamais le reporting. L'inverse a déjà été payé sur les
unlocks DefiLlama, où un plafond appliqué à l'appartenance à la carte faisait
déclarer « aucun calendrier connu » à 37 tokens sur 40 dont le calendrier était
déjà en cache — et comme la renormalisation note mieux un axe absent qu'un axe
mesuré mauvais, le plafond promouvait silencieusement tout ce qu'il sautait.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from cmi_common.events import DeveloperEvent, Source
from cmi_common.kafka import Topic
from cmi_common.observability import EVENTS_PRODUCED, UNMEASURED

from ..domain.activity import RepoStats
from ..domain.aggregate import aggregate
from ..infrastructure.github_client import RepoGone

logger = logging.getLogger(__name__)

SERVICE = "collector-github"

#: ``symbol -> (coin_id, [(owner, repo), ...])``
RepoMap = Mapping[str, tuple[str, list[tuple[str, str]]]]


class SnapshotStore(Protocol):
    async def latest(self, owner: str, repo: str) -> RepoStats | None: ...
    async def save(self, stats: RepoStats) -> None: ...


@dataclass(frozen=True, slots=True)
class _Stats:
    """Ce qu'un cycle a coûté, en comptes séparés.

    Leur somme ne permettrait pas de distinguer « rien n'était éligible » de
    « tout a échoué », deux situations qui appellent des réactions opposées d'un
    opérateur.
    """

    published: int = 0
    refreshed: int = 0
    deferred: int = 0
    gone: int = 0
    failed: int = 0


class GitHubCollector:
    def __init__(
        self,
        *,
        producer,
        store: SnapshotStore,
        fetch_repo: Callable[[str, str], Awaitable[RepoStats]],
        repo_map: Callable[[], RepoMap],
        clock: Callable[[], datetime],
        max_refresh_per_cycle: int = 7,
    ) -> None:
        self._producer = producer
        self._store = store
        self._fetch = fetch_repo
        self._repo_map = repo_map
        self._clock = clock
        self._budget = max_refresh_per_cycle
        #: Curseur tournant sur les dépôts, pour que chacun passe à son tour
        #: plutôt que les premiers affament les suivants.
        self._cursor = 0
        #: Dépôts introuvables, écrits au tableau noir jusqu'au redémarrage.
        self._dead: set[tuple[str, str]] = set()

    async def poll_once(self) -> _Stats:
        mapping = self._repo_map()
        refreshed, deferred, gone, failed = await self._refresh_batch(mapping)
        published = await self._publish_all(mapping)
        stats = _Stats(published, refreshed, deferred, gone, failed)
        logger.info(
            "github cycle: published=%d refreshed=%d deferred=%d gone=%d failed=%d",
            stats.published, stats.refreshed, stats.deferred, stats.gone, stats.failed,
        )
        return stats

    async def _refresh_batch(self, mapping: RepoMap) -> tuple[int, int, int, int]:
        repos = [
            pair
            for _, (_, pairs) in sorted(mapping.items())
            for pair in pairs
            if pair not in self._dead
        ]
        if not repos or self._budget <= 0:
            return 0, 0, 0, 0
        refreshed = deferred = gone = failed = 0
        for offset in range(min(self._budget, len(repos))):
            owner, repo = repos[(self._cursor + offset) % len(repos)]
            try:
                stats = await self._fetch(owner, repo)
            except RepoGone:
                # Renommé, supprimé ou passé privé : réessayer chaque cycle
                # dépenserait un appel par cycle pour la même réponse.
                self._dead.add((owner, repo))
                gone += 1
                continue
            except Exception as exc:  # noqa: BLE001 — un dépôt ne doit pas tuer le cycle
                UNMEASURED.labels(SERVICE, "repo_stats", type(exc).__name__).inc()
                logger.warning("github: %s/%s — %s", owner, repo, exc)
                failed += 1
                continue
            if stats.commits_4w is None:
                # GitHub calcule encore (202). Compté à part : ce n'est ni un
                # succès ni une erreur, et le dépôt repassera au tour suivant.
                deferred += 1
            await self._store.save(stats)
            refreshed += 1
        self._cursor = (self._cursor + min(self._budget, len(repos))) % len(repos)
        return refreshed, deferred, gone, failed

    async def _publish_all(self, mapping: RepoMap) -> int:
        now = self._clock()
        published = 0
        for symbol, (coin_id, pairs) in mapping.items():
            snapshots = [
                snap
                for snap in [await self._store.latest(o, r) for o, r in pairs]
                if snap is not None
            ]
            try:
                activity = aggregate(snapshots, now)
            except Exception as exc:  # noqa: BLE001
                # aggregate() est pur mais pas total : un snapshot partiel
                # (stars absent alors que stars_prev existe) ou un horodatage
                # naïf y lèvent TypeError. Hors de ce try, l'exception sortait
                # de la boucle et coûtait le cycle aux ~249 autres tokens —
                # la garde plus bas ne couvrait que la construction de
                # l'événement, pas le calcul qui la précède.
                UNMEASURED.labels(SERVICE, "aggregate", type(exc).__name__).inc()
                logger.error("github: %s — agrégation impossible: %s", symbol, exc)
                continue
            if activity is None:
                # Aucun dépôt lu pour ce symbole. Publier un événement à zéro
                # transformerait « pas encore mesuré » en « aucune activité »,
                # et l'axe pèserait alors contre le token.
                continue
            if activity.push_timestamp_rejected:
                UNMEASURED.labels(SERVICE, "days_since_push", "clock_skew").inc()
            try:
                event = DeveloperEvent(
                    source=Source.GITHUB,
                    symbol=symbol,
                    coin_id=coin_id,
                    repo_count=activity.repo_count,
                    commit_ratio_4w=activity.commit_ratio_4w,
                    pr_ratio_4w=activity.pr_ratio_4w,
                    days_since_push=activity.days_since_push,
                    star_growth_pct_7d=activity.star_growth_pct_7d,
                    all_repos_archived=activity.all_repos_archived,
                )
            except ValidationError as exc:
                # Un token invalide ne doit pas coûter le cycle aux 249 autres.
                # C'est la leçon de l'incident fees_24h_usd : là-bas, un ge=0
                # sur des frais légitimement négatifs levait dans une boucle
                # d'émission sans garde, et un seul protocole entrant dans
                # l'univers faisait publier *zéro* événement pour tous les
                # tokens du cycle. Le schéma de DeveloperEvent rejette
                # repo_count=0 sans all_repos_archived, ce que aggregate() ne
                # peut pas produire — donc lever ici signale un bug de
                # l'agrégateur, et un bug d'agrégateur sur un token n'est pas
                # une raison de faire taire les autres.
                UNMEASURED.labels(SERVICE, "developer_event", "ValidationError").inc()
                logger.error("github: %s — événement invalide: %s", symbol, exc)
                continue
            await self._producer.publish(Topic.DEVELOPER, event)
            EVENTS_PRODUCED.labels(SERVICE, Topic.DEVELOPER.value, event.event_type).inc()
            published += 1
        return published
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_collector.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/app/application/ tests/test_github_collector.py
git commit -m "feat(collector-github): cycle deux horloges, publication depuis le cache"
```

---

## Task 9 : Câblage du service

**Files:**
- Create: `services/collector-github/app/infrastructure/store.py`, `lists_client.py`, `app/main.py`
- Modify: `docker-compose.yml`, `docker-compose.vps.yml`, `.env.example`
- Modify: `.github/workflows/deploy.yml` — **ne pas oublier**
- Test: `tests/test_github_store.py`

> **Deux éditions distinctes dans `deploy.yml`, et oublier l'une ou l'autre est
> silencieux.**
>
> **a) La matrice d'images.** Chaque service du dépôt figure dans `strategy.matrix.include`.
> Sans la sienne, `docker-compose.vps.yml` référencerait
> `ghcr.io/nbeny/bottrading-collector-github:latest`, une image que la CI ne construit
> jamais, et le déploiement VPS échouerait au `pull` — après un build vert. Ajouter, en
> respectant l'alignement des colonnes :
>
> ```yaml
>           - { name: collector-github,      dockerfile: docker/Dockerfile,     path: services/collector-github }
> ```
>
> **b) La liste blanche de tests.** Le job `test` ne lance pas `pytest tests/` : il énumère
> ~25 chemins de fichiers à la main. **Tout fichier absent de cette liste ne s'exécute
> jamais en CI**, quel que soit son état local. Les treize fichiers de ce chantier doivent
> y être ajoutés, y compris `test_developer_event.py` livré en tâche 1 :
>
> ```
>             tests/test_developer_event.py \
>             tests/test_github_activity.py \
>             tests/test_github_aggregate.py \
>             tests/test_github_lists.py \
>             tests/test_github_client.py \
>             tests/test_github_models.py \
>             tests/test_github_repo_map.py \
>             tests/test_github_collector.py \
>             tests/test_github_store.py \
>             tests/test_github_registry.py \
>             tests/test_haiku_developer_features.py \
>             tests/test_scoring_developer_axis.py \
>             tests/test_axis_parity.py \
> ```
>
> Le job installe `cmi_common` et `api-gateway` seulement ; c'est suffisant, puisque les
> tests chargent le code de service par chemin via `load_service_module` et non par import
> de paquet. `pytest-asyncio` y est déjà, ce dont les tests des tâches 5, 7 et 8 ont besoin.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_store.py
from datetime import UTC, datetime

import pytest

from service_modules import load_service_module

RepoStats = load_service_module("collector-github", "domain.activity").RepoStats
stats_from_rows = load_service_module(
    "collector-github", "infrastructure.store"
).stats_from_rows

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def test_previous_stars_come_from_the_older_row():
    """stars_prev est la lecture precedente, pas la courante."""
    rows = [
        {"stars": 1100, "observed_at": NOW, "commits_4w": 40, "commits_median_52w": 10.0,
         "pr_merged_4w": 8, "pr_merged_52w": 104, "pushed_at": NOW,
         "archived": False, "is_fork": False, "forks": 5},
        {"stars": 1000, "observed_at": NOW, "commits_4w": None, "commits_median_52w": None,
         "pr_merged_4w": None, "pr_merged_52w": None, "pushed_at": None,
         "archived": False, "is_fork": False, "forks": 5},
    ]
    stats = stats_from_rows("o", "r", rows)
    assert stats.stars == 1100
    assert stats.stars_prev == 1000


def test_single_row_leaves_previous_none():
    rows = [
        {"stars": 1000, "observed_at": NOW, "commits_4w": 40, "commits_median_52w": 10.0,
         "pr_merged_4w": 8, "pr_merged_52w": 104, "pushed_at": NOW,
         "archived": False, "is_fork": False, "forks": 5},
    ]
    assert stats_from_rows("o", "r", rows).stars_prev is None


def test_no_rows_returns_none():
    assert stats_from_rows("o", "r", []) is None
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_store.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `infrastructure.store` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/infrastructure/store.py` :

```python
"""Lecture/écriture des snapshots et du registre.

``stats_from_rows`` est pur et testé séparément : c'est lui qui décide ce que
``stars_prev`` vaut, et un décalage d'une ligne y produirait une croissance
d'étoiles fausse mais parfaitement plausible.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, select

from cmi_common.db.models import GithubRepoSnapshot
from cmi_common.db.session import Database

from ..domain.activity import RepoStats

#: Deux lignes suffisent : la courante et celle qui donne le delta d'étoiles.
_HISTORY = 2


def stats_from_rows(
    owner: str, repo: str, rows: Sequence[dict[str, Any]]
) -> RepoStats | None:
    """Reconstruit un ``RepoStats`` depuis les lignes les plus récentes d'abord.

    ``stars_prev`` vient de la deuxième ligne et vaut ``None`` s'il n'y en a
    qu'une : un delta demande deux observations, et le premier passage sur un
    dépôt ne peut rien affirmer sur sa croissance.
    """
    if not rows:
        return None
    current = rows[0]
    previous = rows[1] if len(rows) > 1 else None
    return RepoStats(
        owner=owner,
        repo=repo,
        stars=current.get("stars"),
        forks=current.get("forks"),
        pushed_at=current.get("pushed_at"),
        archived=bool(current.get("archived")),
        is_fork=bool(current.get("is_fork")),
        commits_4w=current.get("commits_4w"),
        commits_median_52w=current.get("commits_median_52w"),
        pr_merged_4w=current.get("pr_merged_4w"),
        pr_merged_52w=current.get("pr_merged_52w"),
        stars_prev=previous.get("stars") if previous else None,
    )


class PostgresSnapshotStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def latest(self, owner: str, repo: str) -> RepoStats | None:
        async with self._db.sessionmaker() as session:
            result = await session.execute(
                select(GithubRepoSnapshot)
                .where(
                    GithubRepoSnapshot.owner == owner,
                    GithubRepoSnapshot.repo == repo,
                )
                .order_by(desc(GithubRepoSnapshot.observed_at))
                .limit(_HISTORY)
            )
            rows = [
                {
                    "stars": row.stars, "forks": row.forks,
                    "observed_at": row.observed_at, "pushed_at": row.pushed_at,
                    "archived": row.archived, "is_fork": row.is_fork,
                    "commits_4w": row.commits_4w,
                    "commits_median_52w": row.commits_median_52w,
                    "pr_merged_4w": row.pr_merged_4w,
                    "pr_merged_52w": row.pr_merged_52w,
                }
                for row in result.scalars()
            ]
        return stats_from_rows(owner, repo, rows)

    async def save(self, stats: RepoStats) -> None:
        async with self._db.sessionmaker() as session:
            session.add(
                GithubRepoSnapshot(
                    owner=stats.owner, repo=stats.repo, stars=stats.stars,
                    forks=stats.forks, commits_4w=stats.commits_4w,
                    commits_median_52w=stats.commits_median_52w,
                    pr_merged_4w=stats.pr_merged_4w,
                    pr_merged_52w=stats.pr_merged_52w,
                    pushed_at=stats.pushed_at, archived=stats.archived,
                    is_fork=stats.is_fork,
                )
            )
            await session.commit()
```

`services/collector-github/app/infrastructure/lists_client.py` :

```python
"""Téléchargement des deux README, sur un timer lent.

Les listes sont régénérées une fois par semaine par leur générateur ; les
relire plus souvent dépenserait de la bande passante pour un contenu identique.
"""

from __future__ import annotations

import httpx

BEST_OF = "https://raw.githubusercontent.com/lukasmasuch/best-of-crypto/main/README.md"
AWESOME = "https://raw.githubusercontent.com/dylanhogg/awesome-crypto/main/README.md"


async def fetch_readmes(
    *, timeout: float = 60.0, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str | None, str | None]:
    """``(best_of, awesome)``; ``None`` pour celle qui n'a pas répondu.

    Un échec sur l'une ne doit pas priver le registre de l'autre : ce sont deux
    sources indépendantes, et ``None`` dit « pas relue ce cycle », ce qui laisse
    les lignes déjà en base intactes.
    """
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as http:
        results: list[str | None] = []
        for url in (BEST_OF, AWESOME):
            try:
                response = await http.get(url, follow_redirects=True)
                response.raise_for_status()
                results.append(response.text)
            except httpx.HTTPError:
                results.append(None)
    return results[0], results[1]
```

`services/collector-github/app/main.py` :

```python
"""collector-github service entrypoint (FastAPI + background poller)."""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI
from sqlalchemy import select

from cmi_common import Settings, create_app
from cmi_common.db.models import Token
from cmi_common.db.session import Database
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .application.collector import GitHubCollector
from .domain.activity import RepoStats
from .infrastructure.github_client import (
    GitHubClient,
    recent_commits,
    weekly_median,
)
from .infrastructure.repo_map import CoinGeckoRepos
from .infrastructure.store import PostgresSnapshotStore

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.getenv("GITHUB_POLL_INTERVAL", "600"))
#: Plancher à 1 : zéro figerait le curseur et le round-robin ne tournerait
#: jamais — une mauvaise configuration qui dégrade en silence.
MAX_REFRESH = max(1, int(os.getenv("GITHUB_MAX_REFRESH_PER_CYCLE", "7")))
UNIVERSE_SIZE = int(os.getenv("GITHUB_UNIVERSE_SIZE", "250"))
TOKEN = os.getenv("GITHUB_TOKEN", "")
PR_WINDOW_DAYS = 28
PR_BASELINE_DAYS = 364


async def _startup(app: FastAPI, settings: Settings) -> None:
    if not TOKEN:
        # Pas de repli sur l'API anonyme (60 req/h) : un quota anonyme épuisé
        # produirait des mesures partielles indiscernables de mesures
        # complètes. Un axe absent est correctement traité en aval ; un axe
        # à moitié mesuré ne l'est pas.
        logger.error("collector-github: GITHUB_TOKEN absent — le service reste inactif")
        return

    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = GitHubClient(token=TOKEN)
    gecko = CoinGeckoRepos()
    store = PostgresSnapshotStore(db)
    mapping: dict[str, tuple[str, list[tuple[str, str]]]] = {}

    async def fetch_repo(owner: str, repo: str) -> RepoStats:
        meta = await client.repo(owner, repo)
        weeks = await client.commit_activity(owner, repo)
        return RepoStats(
            owner=owner,
            repo=repo,
            stars=meta.stars,
            forks=meta.forks,
            pushed_at=meta.pushed_at,
            archived=meta.archived,
            is_fork=meta.is_fork,
            commits_4w=recent_commits(weeks),
            commits_median_52w=weekly_median(weeks),
            pr_merged_4w=await client.merged_pr_count(
                owner, repo, since_days=PR_WINDOW_DAYS
            ),
            pr_merged_52w=await client.merged_pr_count(
                owner, repo, since_days=PR_BASELINE_DAYS
            ),
        )

    collector = GitHubCollector(
        producer=producer,
        store=store,
        fetch_repo=fetch_repo,
        repo_map=lambda: mapping,
        clock=lambda: __import__("datetime").datetime.now(
            tz=__import__("datetime").UTC
        ),
        max_refresh_per_cycle=MAX_REFRESH,
    )

    async def refresh_mapping() -> None:
        """Complète la carte, quelques coins par cycle.

        Le mapping change au rythme des listings ; le remplissage initial
        s'étale sur plusieurs cycles pour tenir dans le quota gratuit de
        CoinGecko plutôt que de le saturer au démarrage.
        """
        async with db.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Token.coin_id, Token.symbol).limit(UNIVERSE_SIZE)
                )
            ).all()
        for coin_id, symbol in rows:
            if not coin_id or not symbol or symbol in mapping:
                continue
            try:
                pairs = await gecko.repos_for(coin_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("coingecko: %s — %s", coin_id, exc)
                continue
            if pairs:
                mapping[symbol] = (coin_id, pairs)
            break  # un coin par cycle : le mapping est quasi immuable

    async def cycle() -> None:
        # Un seul `now` par cycle, échantillonné ici et passé jusqu'à
        # days_since_push. Le relire par dépôt ferait glisser la fenêtre de
        # CLOCK_SKEW_TOLERANCE au fil d'un cycle long, donc rétrécirait
        # silencieusement la tolérance pour les derniers dépôts traités.
        await refresh_mapping()
        await collector.poll_once()

    app.state.db = db
    app.state.producer = producer
    app.state.client = client
    app.state.gecko = gecko
    app.state.poller = asyncio.create_task(
        run_periodic(cycle, POLL_INTERVAL, name="github-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    if not hasattr(app.state, "poller"):
        return
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.client.close()
    await app.state.gecko.close()
    await app.state.producer.stop()
    await app.state.db.dispose()


app = create_app("collector-github", on_startup=_startup, on_shutdown=_shutdown)
```

Dans `docker-compose.yml`, après le bloc `collector-defillama` :

```yaml
  collector-github:
    <<: *service-defaults
    build: { context: ., dockerfile: docker/Dockerfile, args: { SERVICE_PATH: services/collector-github } }
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      kafka:
        condition: service_healthy
    environment:
      <<: *common-env
      GITHUB_TOKEN: ${GITHUB_TOKEN:-}
      GITHUB_POLL_INTERVAL: ${GITHUB_POLL_INTERVAL:-600}
      GITHUB_MAX_REFRESH_PER_CYCLE: ${GITHUB_MAX_REFRESH_PER_CYCLE:-7}
      GITHUB_UNIVERSE_SIZE: ${GITHUB_UNIVERSE_SIZE:-250}
```

Répliquer le même bloc dans `docker-compose.vps.yml` en suivant la forme des autres collectors qui y figurent (image GHCR au lieu de `build`).

Dans `.env.example`, ajouter — **valeur vide, jamais le vrai token** :

```bash
# GitHub — activité de développement. PAT à portée minimale (lecture de dépôts
# publics). Sans lui le service démarre et reste inactif : pas de repli sur
# l'API anonyme, dont le quota de 60 req/h produirait des mesures partielles
# indiscernables de mesures complètes.
GITHUB_TOKEN=
GITHUB_POLL_INTERVAL=600
GITHUB_MAX_REFRESH_PER_CYCLE=7
GITHUB_UNIVERSE_SIZE=250
```

- [ ] **Step 4 : lancer les tests**

Run: `python -m pytest tests/test_github_store.py -v`
Expected: PASS, 3 tests

Run: `docker compose config --quiet`
Expected: aucune sortie (compose valide)

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/ docker-compose.yml docker-compose.vps.yml .env.example tests/test_github_store.py
git commit -m "feat(collector-github): cablage service, compose et configuration"
```

---

## Task 10 : Consommation dans ai-worker-haiku

**Files:**
- Modify: `services/ai-worker-haiku/app/main.py` (liste des topics, ~ligne 47)
- Modify: `services/ai-worker-haiku/app/worker.py` (`_extract`, ~ligne 220)
- Test: `tests/test_haiku_developer_features.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_haiku_developer_features.py
"""HaikuWorker._extract range un DeveloperEvent dans le FeatureStore."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import DeveloperEvent, Source
from cmi_common.kafka import Topic

hw = load_service_module("ai-worker-haiku", "worker")


def _extract(event):
    # Même construction que tests/test_haiku_extract.py : _extract est une
    # méthode liée mais ne touche à aucun collaborateur sur ces branches, donc
    # une instance nue suffit et évite de monter un worker complet.
    worker = hw.HaikuWorker.__new__(hw.HaikuWorker)
    return worker._extract(event)


def _event(**kw):
    base = dict(source=Source.GITHUB, symbol="AAVE", coin_id="aave", repo_count=2)
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


def test_all_repos_archived_false_survives_merge() -> None:
    """False est significatif ici, comme has_unlock_schedule: il dit
    'on a regarde, ce n'est pas mort'."""
    _, fields, _ = _extract(_event(all_repos_archived=False))
    assert fields["all_repos_archived"] is False


def test_developer_event_does_not_trigger_scoring() -> None:
    """Contexte, pas declencheur: scorer un symbole dont on n'a pas le prix
    inventerait une opportunite a partir d'une statistique de depot.

    _ready est une staticmethod prenant le dict de features.
    """
    assert hw.HaikuWorker._ready({"commit_ratio_4w": 1.5}) is False
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_haiku_developer_features.py -v`
Expected: FAIL — `_extract` renvoie `(None, {}, "")` pour un `DeveloperEvent`

- [ ] **Step 3 : implémenter**

Dans `services/ai-worker-haiku/app/main.py`, ajouter `Topic.DEVELOPER` à la liste passée à `EventConsumer`, après `Topic.FUNDAMENTALS` :

```python
            Topic.FUNDAMENTALS,
            Topic.DEVELOPER,
```

Dans `services/ai-worker-haiku/app/worker.py`, importer `DeveloperEvent` avec les autres événements, puis ajouter la branche dans `_extract`, juste après celle de `FundamentalsEvent` et **avant** le `return None, {}, ""` final :

```python
        if isinstance(event, DeveloperEvent):
            # Contexte, pas déclencheur — même statut que DerivativesEvent :
            # _ready() n'est délibérément pas relâché pour ces événements.
            # Scorer un symbole dont on n'a pas le prix inventerait une
            # opportunité à partir d'une statistique de dépôt.
            #
            # all_repos_archived est un bool dont False est significatif, comme
            # has_unlock_schedule : le store ne laisse tomber que les None à la
            # fusion, donc False survit — ce qui garde « on a regardé, ce n'est
            # pas mort » distinct de « on n'a pas regardé ».
            return (
                event.symbol,
                {
                    "commit_ratio_4w": event.commit_ratio_4w,
                    "pr_ratio_4w": event.pr_ratio_4w,
                    "days_since_push": event.days_since_push,
                    "star_growth_pct_7d": event.star_growth_pct_7d,
                    "all_repos_archived": event.all_repos_archived,
                    "dev_repo_count": event.repo_count,
                },
                Topic.DEVELOPER.value,
            )
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_haiku_developer_features.py -v`
Expected: PASS, 4 tests

Run: `python -m pytest tests/ -k haiku -v`
Expected: PASS — aucune régression sur les tests haiku existants

- [ ] **Step 5 : commit**

```bash
git add services/ai-worker-haiku/app/ tests/test_haiku_developer_features.py
git commit -m "feat(ai-worker-haiku): ranger DeveloperEvent dans le FeatureStore"
```

---

## Task 11 : Le huitième axe dans le scoring

**Files:**
- Modify: `services/decision-engine/app/scoring.py` (`WEIGHTS` ~ligne 37, `Features` ~ligne 70, `score()` ~ligne 251)
- Modify: `services/decision-engine/app/engine.py` (`_on_analysis` ~ligne 139)
- Test: `tests/test_scoring_developer_axis.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_scoring_developer_axis.py
import pytest

from service_modules import load_service_module

_scoring = load_service_module("decision-engine", "scoring")
WEIGHTS = _scoring.WEIGHTS
Features = _scoring.Features
_norm_developer_activity = _scoring._norm_developer_activity
score = _scoring.score


def test_weights_still_sum_to_one():
    """_MIN_PRESENT_WEIGHT = 0.20 est un seuil sur cette somme: la changer
    modifierait silencieusement le sens de la porte."""
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_developer_activity_has_weight():
    assert WEIGHTS["developer_activity"] == pytest.approx(0.08)


def test_habitual_pace_scores_mid():
    v = _norm_developer_activity(
        commit_ratio=1.0, pr_ratio=None, days_since_push=None,
        star_growth=None, all_archived=False,
    )
    assert v == pytest.approx(0.5)


def test_tripled_activity_scores_max():
    v = _norm_developer_activity(
        commit_ratio=3.0, pr_ratio=None, days_since_push=None,
        star_growth=None, all_archived=False,
    )
    assert v == pytest.approx(1.0)


def test_collapsed_activity_scores_min():
    v = _norm_developer_activity(
        commit_ratio=1 / 3, pr_ratio=None, days_since_push=None,
        star_growth=None, all_archived=False,
    )
    assert v == pytest.approx(0.0)


def test_stopped_project_is_a_measured_zero():
    """Ratio 0 avec une baseline reelle: le projet s'est arrete. C'est mesure."""
    v = _norm_developer_activity(
        commit_ratio=0.0, pr_ratio=None, days_since_push=None,
        star_growth=None, all_archived=False,
    )
    assert v == 0.0


def test_absent_everything_is_none_not_zero():
    v = _norm_developer_activity(
        commit_ratio=None, pr_ratio=None, days_since_push=None,
        star_growth=None, all_archived=False,
    )
    assert v is None


def test_all_archived_is_zero_not_none():
    """On a regarde, tout est archive: une observation, pas une absence."""
    v = _norm_developer_activity(
        commit_ratio=None, pr_ratio=None, days_since_push=None,
        star_growth=None, all_archived=True,
    )
    assert v == 0.0


def test_absent_axis_is_excluded_from_breakdown():
    result = score(Features(price_change_pct_24h=5.0, volume_spike_ratio=2.0,
                            liquidity_usd=1e6, sentiment_score=0.5))
    assert "developer_activity" not in result.breakdown


def test_present_axis_appears_in_breakdown():
    result = score(Features(price_change_pct_24h=5.0, volume_spike_ratio=2.0,
                            liquidity_usd=1e6, sentiment_score=0.5,
                            commit_ratio_4w=2.0))
    assert "developer_activity" in result.breakdown
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_scoring_developer_axis.py -v`
Expected: FAIL — `KeyError: 'developer_activity'` et `ImportError` sur `_norm_developer_activity`

- [ ] **Step 3 : implémenter**

Dans `services/decision-engine/app/scoring.py`, remplacer `WEIGHTS` :

```python
WEIGHTS = {
    # Rescalés ×0.92 lors de l'ajout de developer_activity, pour que la somme
    # reste exactement 1.0 : _MIN_PRESENT_WEIGHT est un seuil sur cette somme,
    # et la laisser dériver à 1.08 aurait changé le sens de la porte sans que
    # rien ne le signale.
    "volume_growth": 0.1725,
    "social_score": 0.1380,
    "news_score": 0.1380,
    "market_trend": 0.1380,
    "liquidity_score": 0.1035,
    "positioning": 0.1380,
    "fundamentals": 0.0920,
    "developer_activity": 0.0800,
}
```

Ajouter à `Features`, après `has_unlock_schedule` :

```python
    #: Activité GitHub. Ratios bruts : 1.0 = le projet avance à son rythme
    #: habituel. all_repos_archived True est un constat de mort mesuré, à
    #: distinguer de l'absence de toute lecture — voir _norm_developer_activity.
    commit_ratio_4w: float | None = None
    pr_ratio_4w: float | None = None
    days_since_push: int | None = None
    star_growth_pct_7d: float | None = None
    all_repos_archived: bool = False
```

Ajouter les constantes près des autres seuils du module :

```python
#: Un projet qui triple son rythme sature l'échelle ; un projet tombé au tiers
#: la vide. Symétrique par construction, ce que fait le logarithme.
_MOMENTUM_SPAN = 3.0
_FRESH_DAYS = 7.0
_STALE_DAYS = 90.0
#: +2 % d'étoiles en une semaine sature le terme.
_STAR_GROWTH_FULL = 0.02
_DEV_SUB_WEIGHTS = {
    "commit": 0.40,
    "pr": 0.25,
    "freshness": 0.25,
    "stars": 0.10,
}
```

Ajouter la fonction après `_norm_fundamentals` :

```python
def _norm_developer_activity(
    *,
    commit_ratio: float | None,
    pr_ratio: float | None,
    days_since_push: int | None,
    star_growth: float | None,
    all_archived: bool,
) -> float | None:
    """Momentum de développement relatif au projet, dans [0, 1].

    Relatif, et non absolu : Bitcoin a 1 200 contributeurs et un token récent
    en a quatre. Un axe bâti sur des volumes bruts classerait mécaniquement les
    grandes capitalisations en tête et ne dirait rien que le pipeline ne sache
    déjà — il dupliquerait la capitalisation sous un autre nom.

    ``all_archived`` court-circuite : tous les dépôts connus sont archivés ou
    forkés, ce qui est un zéro **mesuré**. C'est le seul de la chaîne. Tout le
    reste, absent, laisse l'axe à ``None`` et donc exclu de la renormalisation.
    """
    if all_archived:
        return 0.0
    terms = {
        "commit": _momentum(commit_ratio),
        "pr": _momentum(pr_ratio),
        "freshness": _freshness(days_since_push),
        "stars": (
            None
            if star_growth is None
            else max(0.0, min(1.0, star_growth / _STAR_GROWTH_FULL))
        ),
    }
    present = {k: v for k, v in terms.items() if v is not None}
    if not present:
        return None
    weight = sum(_DEV_SUB_WEIGHTS[k] for k in present)
    return sum(present[k] * _DEV_SUB_WEIGHTS[k] for k in present) / weight


def _momentum(ratio: float | None) -> float | None:
    """Ratio d'activité mis à l'échelle, 1.0 (rythme habituel) valant 0.5.

    Un ratio de 0 avec une baseline réelle vaut 0.0 : le projet commitait et
    s'est arrêté, ce qui est une observation. Le collector rend ``None`` — et
    non 0 — quand la baseline elle-même est indisponible, donc les deux cas ne
    se confondent pas ici.
    """
    if ratio is None:
        return None
    if ratio <= 0:
        return 0.0
    return max(0.0, min(1.0, 0.5 + 0.5 * math.log(ratio) / math.log(_MOMENTUM_SPAN)))


def _freshness(days: int | None) -> float | None:
    if days is None:
        return None
    if days <= _FRESH_DAYS:
        return 1.0
    if days >= _STALE_DAYS:
        return 0.0
    return 1.0 - (days - _FRESH_DAYS) / (_STALE_DAYS - _FRESH_DAYS)
```

Dans `score()`, ajouter l'entrée après `"fundamentals"` :

```python
        "developer_activity": _norm_developer_activity(
            commit_ratio=features.commit_ratio_4w,
            pr_ratio=features.pr_ratio_4w,
            days_since_push=features.days_since_push,
            star_growth=features.star_growth_pct_7d,
            all_archived=features.all_repos_archived,
        ),
```

Dans `services/decision-engine/app/engine.py`, ajouter à la construction de `Features` dans `_on_analysis`, après `has_unlock_schedule` :

```python
            commit_ratio_4w=raw.get("commit_ratio_4w"),
            pr_ratio_4w=raw.get("pr_ratio_4w"),
            days_since_push=raw.get("days_since_push"),
            star_growth_pct_7d=raw.get("star_growth_pct_7d"),
            all_repos_archived=bool(raw.get("all_repos_archived")),
```

- [ ] **Step 4 : lancer les tests**

Run: `python -m pytest tests/test_scoring_developer_axis.py -v`
Expected: PASS, 10 tests

Run: `python -m pytest tests/ -k scoring -v`
Expected: PASS — les tests de scoring existants ne doivent pas régresser. Si l'un d'eux fige une valeur de score numérique, la rebalance des poids l'aura décalée : recalculer la valeur attendue à la main et l'ajuster, ne jamais assouplir l'assertion.

- [ ] **Step 5 : commit**

```bash
git add services/decision-engine/app/ tests/test_scoring_developer_axis.py
git commit -m "feat(scoring): axe developer_activity, poids rescales a 1.0"
```

---

## Task 12 : Les trois copies et leur garde-fou

**Files:**
- Modify: `services/api-gateway/app/dossier.py:30-38`
- Modify: `frontend/src/lib/types/dossier.ts:13-33`
- Test: `tests/test_axis_parity.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_axis_parity.py
"""La liste d'axes existe en trois copies independantes, dont aucune n'importe
les autres — api-gateway ne doit pas dependre de decision-engine. Rien ne
verifiait qu'elles restent alignees: un huitieme axe oublie dans l'une des trois
serait simplement invisible dans le drawer /market, sans erreur ni test rouge.

Ce test lit les trois fichiers par analyse syntaxique plutot que par import,
pour ne dependre d'aucun chemin de package.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORING = ROOT / "services/decision-engine/app/scoring.py"
DOSSIER_PY = ROOT / "services/api-gateway/app/dossier.py"
DOSSIER_TS = ROOT / "frontend/src/lib/types/dossier.ts"


def _assigned(path: Path, name: str) -> ast.expr:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    raise AssertionError(f"{name} introuvable dans {path}")


def _weights_keys() -> list[str]:
    node = _assigned(SCORING, "WEIGHTS")
    return [k.value for k in node.keys if isinstance(k, ast.Constant)]


def _axis_keys() -> list[str]:
    node = _assigned(DOSSIER_PY, "AXIS_KEYS")
    return [e.value for e in node.elts if isinstance(e, ast.Constant)]


def _score_axes() -> list[str]:
    text = DOSSIER_TS.read_text(encoding="utf-8")
    block = re.search(r"export const SCORE_AXES\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert block, "SCORE_AXES introuvable"
    return re.findall(r"'([^']+)'", block.group(1))


def _axis_labels() -> set[str]:
    text = DOSSIER_TS.read_text(encoding="utf-8")
    block = re.search(r"export const AXIS_LABELS[^{]*\{(.*?)\}", text, re.DOTALL)
    assert block, "AXIS_LABELS introuvable"
    return set(re.findall(r"^\s*(\w+)\s*:", block.group(1), re.MULTILINE))


def test_the_three_copies_agree():
    weights, axes, ts = set(_weights_keys()), set(_axis_keys()), set(_score_axes())
    assert weights == axes, f"scoring.py vs dossier.py: {weights ^ axes}"
    assert weights == ts, f"scoring.py vs dossier.ts: {weights ^ ts}"


def test_order_is_identical():
    """Le drawer affiche les axes dans l'ordre de la liste: un ordre divergent
    donnerait deux lectures differentes du meme score."""
    assert _axis_keys() == _score_axes()


def test_every_axis_has_a_label():
    assert set(_score_axes()) <= _axis_labels()


def test_developer_activity_is_present_everywhere():
    assert "developer_activity" in _weights_keys()
    assert "developer_activity" in _axis_keys()
    assert "developer_activity" in _score_axes()
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_axis_parity.py -v`
Expected: FAIL — `scoring.py vs dossier.py: {'developer_activity'}`

- [ ] **Step 3 : implémenter**

Dans `services/api-gateway/app/dossier.py`, remplacer `AXIS_KEYS` :

```python
#: Une des trois copies de la liste d'axes (avec decision-engine/scoring.py et
#: frontend/dossier.ts). Aucune n'importe les autres — api-gateway ne doit pas
#: dépendre de decision-engine — donc tests/test_axis_parity.py est ce qui les
#: tient alignées. Les trois bougent ensemble, ou le nouvel axe est invisible.
AXIS_KEYS: tuple[str, ...] = (
    "volume_growth",
    "social_score",
    "news_score",
    "market_trend",
    "liquidity_score",
    "positioning",
    "fundamentals",
    "developer_activity",
)
```

Dans `frontend/src/lib/types/dossier.ts` :

```typescript
export const SCORE_AXES = [
  'volume_growth',
  'social_score',
  'news_score',
  'market_trend',
  'liquidity_score',
  'positioning',
  'fundamentals',
  'developer_activity',
] as const;

export type ScoreAxis = (typeof SCORE_AXES)[number];

export const AXIS_LABELS: Record<ScoreAxis, string> = {
  volume_growth: 'Volume',
  social_score: 'Social',
  news_score: 'News',
  market_trend: 'Tendance',
  liquidity_score: 'Liquidité',
  positioning: 'Positionnement',
  fundamentals: 'Fondamentaux',
  developer_activity: 'Développement',
};
```

**Attention à l'ordre :** `AXIS_KEYS` place `liquidity_score` avant `positioning`, alors que `WEIGHTS` dans `scoring.py` a `positioning` avant `liquidity_score`. `test_order_is_identical` ne compare que `dossier.py` et `dossier.ts` entre eux, pas avec `WEIGHTS` — un dictionnaire de poids n'a pas vocation à porter un ordre d'affichage. Garder l'ordre existant de `AXIS_KEYS` et n'ajouter `developer_activity` qu'à la fin des deux.

- [ ] **Step 4 : lancer les tests**

Run: `python -m pytest tests/test_axis_parity.py -v`
Expected: PASS, 4 tests

Run: `cd frontend && npx tsc --noEmit`
Expected: aucune erreur — `AXIS_LABELS` étant un `Record<ScoreAxis, string>`, un axe ajouté à `SCORE_AXES` sans label casserait la compilation

- [ ] **Step 5 : commit**

```bash
git add services/api-gateway/app/dossier.py frontend/src/lib/types/dossier.ts tests/test_axis_parity.py
git commit -m "feat(dossier): propager developer_activity et verrouiller les trois copies"
```

---

## Task 13 : Harnais de vérification live

**Files:**
- Create: `scripts/verify_github_activity.py`

- [ ] **Step 1 : écrire le script**

Pas de test unitaire ici : c'est un outil d'observation contre l'API réelle, dans la lignée de `scripts/verify_read_live.py`. Sa sortie est ce qui tranche le critère de succès n°3 du spec.

```python
#!/usr/bin/env python
"""Sort la distribution réelle de l'axe developer_activity sur l'univers suivi.

C'est le test de vérité avant de faire confiance au poids de 0.08. Deux
dégénérescences le disqualifieraient, et aucune ne ferait échouer un test
unitaire :

* **concentration** — si presque tous les tokens tombent dans le même dixième,
  l'axe ne discrimine rien et ne fait qu'ajouter du bruit au score ;
* **corrélation au rang** — si l'ordre produit reproduit le classement par
  capitalisation, l'axe redit ce que le pipeline sait déjà, ce qui était
  précisément le reproche fait au cadrage en niveau absolu.

Usage :
    GITHUB_TOKEN=... python scripts/verify_github_activity.py [--limit 50]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from datetime import UTC, datetime

# Ce script touche deux services, et chacun embarque un package nommé `app`.
# Un double `sys.path.insert` ne marche pas : le premier `import app` fige
# `sys.modules["app"]` sur collector-github, et l'import suivant chercherait
# `app.scoring` dedans. On réutilise donc le chargeur des tests, qui enregistre
# chaque service sous un alias distinct.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from service_modules import load_service_module  # noqa: E402

_activity = load_service_module("collector-github", "domain.activity")
_gh = load_service_module("collector-github", "infrastructure.github_client")
RepoStats = _activity.RepoStats
aggregate = load_service_module("collector-github", "domain.aggregate").aggregate
GitHubClient, RepoGone = _gh.GitHubClient, _gh.RepoGone
recent_commits, weekly_median = _gh.recent_commits, _gh.weekly_median
CoinGeckoRepos = load_service_module(
    "collector-github", "infrastructure.repo_map"
).CoinGeckoRepos
_norm_developer_activity = load_service_module(
    "decision-engine", "scoring"
)._norm_developer_activity

#: Univers d'observation par défaut : assez large pour voir une distribution,
#: assez court pour tenir en quelques minutes de quota.
DEFAULT_COINS = [
    "bitcoin", "ethereum", "solana", "cardano", "polkadot", "chainlink",
    "avalanche-2", "uniswap", "aave", "the-graph", "maker", "curve-dao-token",
    "lido-dao", "arbitrum", "optimism", "cosmos", "near", "algorand",
    "filecoin", "internet-computer",
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(DEFAULT_COINS))
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN absent", file=sys.stderr)
        return 2

    now = datetime.now(tz=UTC)
    client = GitHubClient(token=token)
    gecko = CoinGeckoRepos()
    scores: list[tuple[str, float | None, int]] = []

    try:
        for rank, coin_id in enumerate(DEFAULT_COINS[: args.limit], start=1):
            try:
                pairs = await gecko.repos_for(coin_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  {coin_id}: mapping indisponible — {exc}", file=sys.stderr)
                continue
            snapshots = []
            for owner, repo in pairs[:3]:
                try:
                    meta = await client.repo(owner, repo)
                    weeks = await client.commit_activity(owner, repo)
                except RepoGone:
                    continue
                snapshots.append(
                    RepoStats(
                        owner=owner, repo=repo, stars=meta.stars, forks=meta.forks,
                        pushed_at=meta.pushed_at, archived=meta.archived,
                        is_fork=meta.is_fork, commits_4w=recent_commits(weeks),
                        commits_median_52w=weekly_median(weeks),
                        pr_merged_4w=await client.merged_pr_count(owner, repo, since_days=28),
                        pr_merged_52w=await client.merged_pr_count(owner, repo, since_days=364),
                    )
                )
            activity = aggregate(snapshots, now)
            value = (
                None
                if activity is None
                else _norm_developer_activity(
                    commit_ratio=activity.commit_ratio_4w,
                    pr_ratio=activity.pr_ratio_4w,
                    days_since_push=activity.days_since_push,
                    star_growth=activity.star_growth_pct_7d,
                    all_archived=activity.all_repos_archived,
                )
            )
            scores.append((coin_id, value, rank))
            shown = "—" if value is None else f"{value:.3f}"
            print(f"{coin_id:24s} rang={rank:3d}  axe={shown}")
    finally:
        await client.close()
        await gecko.close()

    measured = [v for _, v, _ in scores if v is not None]
    print(f"\ncouverture : {len(measured)}/{len(scores)} tokens mesurés")
    if len(measured) < 3:
        print("trop peu de mesures pour juger la distribution")
        return 1
    print(f"médiane    : {statistics.median(measured):.3f}")
    print(f"écart-type : {statistics.pstdev(measured):.3f}")
    deciles = [0] * 10
    for value in measured:
        deciles[min(9, int(value * 10))] += 1
    print(f"déciles    : {deciles}")
    if max(deciles) > 0.7 * len(measured):
        print("DÉGÉNÉRÉ : plus de 70 % des tokens dans un seul décile")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2 : exécuter contre l'API réelle**

Run: `GITHUB_TOKEN=<le token régénéré> python scripts/verify_github_activity.py`
Expected: une ligne par token, puis couverture, médiane, écart-type et déciles. La couverture doit atteindre au moins 60 % (critère n°1 du spec) et les déciles ne doivent pas être concentrés.

> **Sortir aussi la distribution du seul sous-signal `star_growth`, séparément de l'axe.**
> Son seuil de saturation est à 2 % sur 7 jours : exigeant pour un dépôt à 10 000 étoiles
> (il faut en gagner 100), trivial pour un dépôt à 50 (une seule étoile en 12 h se
> normalise à 14 % et sature). Si le sous-signal sature pour la majorité des petits
> dépôts, il n'ordonne plus rien et vaut du bruit à 0.10 du poids de l'axe. Deux issues :
> relever le seuil, ou exiger une base absolue minimale d'étoiles en dessous de laquelle
> la croissance relative rend `None`. Trancher sur les données réelles, pas a priori —
> c'est exactement pour ce genre d'arbitrage que ce script existe.

- [ ] **Step 3 : consigner le résultat**

Reporter la sortie dans le spec, sous une section « Distribution observée », avec la date. Si la distribution est dégénérée, ramener `WEIGHTS["developer_activity"]` à `0.0` — l'axe reste alors observable dans le drawer sans influencer le score — et le noter également.

- [ ] **Step 4 : lancer la suite complète**

Run: `make lint`
Expected: ruff, black et mypy passent

Run: `make test`
Expected: toute la suite passe

- [ ] **Step 5 : commit**

```bash
git add scripts/verify_github_activity.py docs/superpowers/specs/2026-08-02-github-ingestion-design.md
git commit -m "feat(scripts): harnais de verification de la distribution de l'axe"
```

---

## Task 14 : Peuplement du registre depuis les listes

C'est la moitié « catalogue » de la demande : récupérer les URLs GitHub **et** les URLs des
sites officiels des deux README, et les persister. Dépend des tâches 4 (parsing), 6 (tables)
et 7 (promotion).

**Files:**
- Create: `services/collector-github/app/application/registry.py`
- Modify: `services/collector-github/app/main.py` (timer lent)
- Test: `tests/test_github_registry.py`

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/test_github_registry.py
import pytest
from service_modules import load_service_module

ListEntry = load_service_module("collector-github", "domain.lists").ListEntry
_registry = load_service_module("collector-github", "application.registry")
merge_entries = _registry.merge_entries


def _entry(name, owner, repo, homepage=None, source="best-of-crypto"):
    return ListEntry(
        name=name, owner=owner, repo=repo, homepage_url=homepage, source_list=source
    )


def test_same_repo_in_both_lists_is_one_row():
    """Bitcoin Core figure dans les deux listes: une seule ligne de registre."""
    merged = merge_entries(
        [_entry("bitcoin", "bitcoin", "bitcoin")],
        [_entry("bitcoin", "bitcoin", "bitcoin", "https://bitcoincore.org", "awesome-crypto")],
    )
    assert len(merged) == 1


def test_homepage_from_awesome_wins_over_absence():
    """best-of n'en publie pas; la fusion ne doit pas ecraser celle d'awesome."""
    merged = merge_entries(
        [_entry("bitcoin", "bitcoin", "bitcoin")],
        [_entry("bitcoin", "bitcoin", "bitcoin", "https://bitcoincore.org", "awesome-crypto")],
    )
    assert merged[0].homepage_url == "https://bitcoincore.org"


def test_absent_homepage_stays_none():
    """Aucune des deux listes n'en publie: None, pas une URL deduite du repo."""
    merged = merge_entries([_entry("ccxt", "ccxt", "ccxt")], [])
    assert merged[0].homepage_url is None


def test_source_list_records_both_when_present():
    merged = merge_entries(
        [_entry("bitcoin", "bitcoin", "bitcoin")],
        [_entry("bitcoin", "bitcoin", "bitcoin", None, "awesome-crypto")],
    )
    assert merged[0].source_list == "best-of-crypto,awesome-crypto"


def test_entries_unique_to_one_list_survive():
    merged = merge_entries(
        [_entry("a", "o", "a")], [_entry("b", "o", "b", None, "awesome-crypto")]
    )
    assert {e.repo for e in merged} == {"a", "b"}
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_github_registry.py -v`
Expected: FAIL — `load_service_module` ne trouve pas `application.registry` (FileNotFoundError)

- [ ] **Step 3 : implémenter**

`services/collector-github/app/application/registry.py` :

```python
"""Peuplement du registre de projets depuis les deux awesome-lists.

Les deux listes se recouvrent partiellement — Bitcoin Core figure dans les deux —
et ne publient pas la même chose : seule ``awesome-crypto`` porte une URL de site
officiel. La fusion est donc asymétrique par nécessité, et jamais inventive : un
projet que ni l'une ni l'autre ne documente garde ``homepage_url = None`` plutôt
qu'une URL déduite du dépôt.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from cmi_common.db.models import CryptoProjectRegistry
from cmi_common.db.session import Database

from ..domain.lists import ListEntry


def merge_entries(
    best_of: Iterable[ListEntry], awesome: Iterable[ListEntry]
) -> list[ListEntry]:
    """Fusionne les deux listes sur ``(owner, repo)``.

    L'URL de site de la seconde liste complète la première, qui n'en publie
    aucune. L'inverse ne peut pas arriver, mais la règle est écrite comme « la
    valeur présente gagne sur l'absente » plutôt que « awesome gagne », pour que
    la fusion reste correcte si l'une des listes change de contenu.
    """
    merged: dict[tuple[str, str], ListEntry] = {}
    for entry in [*best_of, *awesome]:
        key = (entry.owner, entry.repo)
        current = merged.get(key)
        if current is None:
            merged[key] = entry
            continue
        sources = current.source_list.split(",")
        if entry.source_list not in sources:
            sources.append(entry.source_list)
        merged[key] = replace(
            current,
            homepage_url=current.homepage_url or entry.homepage_url,
            description=current.description or entry.description,
            source_list=",".join(sources),
        )
    return list(merged.values())


async def persist(db: Database, entries: Sequence[ListEntry]) -> int:
    """Upsert sur ``github_url``; ``last_seen_at`` est rafraîchi à chaque passage.

    ``first_seen_at`` n'est jamais écrasé : c'est la seule trace de la date
    d'entrée d'un projet dans les listes, et la seule chose qui rendrait un jour
    exploitable le signal « nouvellement listé » que ce spec écarte pour l'instant.
    """
    if not entries:
        return 0
    written = 0
    async with db.sessionmaker() as session:
        for entry in entries:
            statement = insert(CryptoProjectRegistry).values(
                github_url=entry.github_url,
                name=entry.name,
                homepage_url=entry.homepage_url,
                description=entry.description,
                source_list=entry.source_list,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["github_url"],
                    set_={
                        "name": statement.excluded.name,
                        "homepage_url": statement.excluded.homepage_url,
                        "description": statement.excluded.description,
                        "source_list": statement.excluded.source_list,
                        "last_seen_at": _now(),
                    },
                )
            )
            written += 1
        await session.commit()
    return written


def _now():
    from sqlalchemy import func

    return func.now()


async def resolved_symbols(db: Database) -> dict[str, str]:
    """``github_url -> symbol`` pour les seules lignes dont le ticker est résolu.

    Les lignes à ``symbol NULL`` sont volontairement absentes : elles restent au
    catalogue mais ne doivent alimenter aucun agrégat.
    """
    async with db.sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    CryptoProjectRegistry.github_url, CryptoProjectRegistry.symbol
                ).where(CryptoProjectRegistry.symbol.is_not(None))
            )
        ).all()
    return {url: symbol for url, symbol in rows}
```

Dans `services/collector-github/app/main.py`, ajouter la constante et le timer lent :

```python
LISTS_REFRESH_SECONDS = float(os.getenv("GITHUB_LISTS_REFRESH_HOURS", "168")) * 3600
```

puis, dans `_startup`, à côté du poller principal :

```python
    async def refresh_lists() -> None:
        """Relit les deux README et met le registre à jour.

        Les listes sont régénérées une fois par semaine par leur générateur ;
        les relire plus souvent dépenserait de la bande passante pour un
        contenu identique.
        """
        best_of_md, awesome_md = await fetch_readmes()
        entries = merge_entries(
            parse_best_of_crypto(best_of_md) if best_of_md else [],
            parse_awesome_crypto(awesome_md) if awesome_md else [],
        )
        written = await persist(db, entries)
        logger.info("github: registre mis a jour, %d projets", written)

    app.state.lists_poller = asyncio.create_task(
        run_periodic(refresh_lists, LISTS_REFRESH_SECONDS, name="github-lists")
    )
```

avec les imports correspondants en tête du fichier :

```python
from .application.registry import merge_entries, persist
from .domain.lists import parse_awesome_crypto, parse_best_of_crypto
from .infrastructure.lists_client import fetch_readmes
```

et l'annulation dans `_shutdown`, à côté de celle de `app.state.poller` :

```python
    app.state.lists_poller.cancel()
    await asyncio.gather(app.state.lists_poller, return_exceptions=True)
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_github_registry.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5 : commit**

```bash
git add services/collector-github/app/application/registry.py services/collector-github/app/main.py tests/test_github_registry.py
git commit -m "feat(collector-github): peupler le registre depuis les deux listes"
```

---

## Couverture du spec

| Exigence du spec | Tâche |
|---|---|
| `DeveloperEvent`, `Topic.DEVELOPER`, `EventType`, `Source` | 1 |
| `domain/activity.py`, ratios, `None` vs `0` | 2 |
| `domain/aggregate.py`, archivés exclus, zéro mesuré | 3 |
| Parsing des deux README, homepage absente sur best-of | 4 |
| Client REST : 202, 404, deux seaux de quota | 5 |
| Trois tables + migration | 6 |
| Mapping CoinGecko + promotion filtrée, homographes | 7 |
| Deux horloges, budget round-robin, compteurs séparés | 8 |
| `main.py`, compose, `.env.example`, pas de repli anonyme | 9 |
| Consommation haiku, contexte et non déclencheur | 10 |
| Axe, poids rescalés à 1.0, `_MIN_PRESENT_WEIGHT` préservé | 11 |
| Trois copies + test de parité | 12 |
| `scripts/verify_github_activity.py`, critères de succès | 13 |
| Registre peuplé, URLs de sites, rafraîchissement hebdomadaire | 14 |

Deux limites assumées de ce plan, énoncées plutôt que masquées.

`promote_list_entries` (tâche 7) est écrit et testé, mais aucune tâche ne le branche sur
`coin_repo_map` : la carte est alimentée par le seul CoinGecko en tâche 9. C'est délibéré —
la promotion depuis les listes n'a de valeur qu'une fois mesurée la couverture réelle de
CoinGecko, que le critère de succès n°1 chiffre à l'exécution de la tâche 13. Si elle
dépasse 60 %, la promotion peut rester inutilisée ; sinon elle se branche en une dizaine de
lignes dans `refresh_mapping`.

L'exposition du registre par l'API de lecture n'est pas planifiée. Ce serait une évolution
du drawer `/market` avec son propre passage par les trois copies du contrat, hors du
périmètre de ce spec.
