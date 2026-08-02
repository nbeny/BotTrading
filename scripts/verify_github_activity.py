#!/usr/bin/env python
"""Sort la distribution réelle de l'axe developer_activity sur l'univers suivi.

C'est le test de vérité avant de faire confiance au poids de 0.08. Deux
dégénérescences le disqualifieraient, et aucune ne ferait échouer un test
unitaire :

* **concentration** — si presque tous les tokens tombent dans le même dixième,
  l'axe ne discrimine rien et n'ajoute que du bruit au score ;
* **corrélation au rang** — si l'ordre produit reproduit le classement par
  capitalisation, l'axe redit ce que le pipeline sait déjà, ce qui était
  précisément le reproche fait au cadrage en niveau absolu.

Le sous-signal `star_growth` est sorti **séparément** : son seuil de saturation
est à 2 % sur 7 jours, exigeant pour un dépôt à 10 000 étoiles et trivial pour
un dépôt à 50. S'il sature pour la majorité des petits dépôts, il n'ordonne
plus rien et vaut du bruit à 0.10 du poids de l'axe.

Usage :
    GITHUB_TOKEN=... python scripts/verify_github_activity.py [--limit 20]
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
_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "tests"))
sys.path.insert(0, os.path.join(_ROOT, "libs", "cmi_common"))

from service_modules import load_service_module  # noqa: E402

_activity = load_service_module("collector-github", "domain.activity")
_gh = load_service_module("collector-github", "infrastructure.github_client")
RepoStats = _activity.RepoStats
aggregate = load_service_module("collector-github", "domain.aggregate").aggregate
GitHubClient, RepoGoneError = _gh.GitHubClient, _gh.RepoGoneError
recent_commits, weekly_median = _gh.recent_commits, _gh.weekly_median
CoinGeckoRepos = load_service_module(
    "collector-github", "infrastructure.repo_map"
).CoinGeckoRepos
_scoring = load_service_module("decision-engine", "scoring")
_norm_developer_activity = _scoring._norm_developer_activity

#: Univers d'observation par défaut : assez large pour voir une distribution,
#: assez court pour tenir en quelques minutes de quota. Volontairement mélangé
#: entre géants et mid-caps, puisque c'est là que l'axe doit discriminer.
DEFAULT_COINS = [
    "bitcoin",
    "ethereum",
    "solana",
    "cardano",
    "polkadot",
    "chainlink",
    "avalanche-2",
    "uniswap",
    "aave",
    "the-graph",
    "maker",
    "curve-dao-token",
    "lido-dao",
    "arbitrum",
    "optimism",
    "cosmos",
    "near",
    "algorand",
    "filecoin",
    "internet-computer",
]
#: Au plus trois dépôts par coin : au-delà, le quota part dans la longue traîne
#: de bibliothèques sans que la mesure change.
MAX_REPOS_PER_COIN = 3
DEGENERATE_SHARE = 0.7


async def _read_repo(client, owner: str, repo: str, now: datetime) -> RepoStats | None:
    try:
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
            pr_merged_4w=await client.merged_pr_count(owner, repo, since_days=28),
            pr_merged_52w=await client.merged_pr_count(owner, repo, since_days=364),
            stars_at=now,
            # Le script ne dispose pas d'un snapshot précédent : la croissance
            # d'étoiles est donc structurellement absente ici, et c'est ce que
            # la ligne « couverture » doit refléter plutôt que masquer.
            stars_prev=None,
            stars_prev_at=None,
        )
    except RepoGoneError:
        return None


def _deciles(values: list[float]) -> list[int]:
    buckets = [0] * 10
    for value in values:
        buckets[min(9, int(value * 10))] += 1
    return buckets


def _report(label: str, values: list[float]) -> None:
    if len(values) < 3:
        print(f"{label}: trop peu de mesures ({len(values)}) pour juger")
        return
    buckets = _deciles(values)
    print(f"\n{label}")
    print(f"  mediane    : {statistics.median(values):.3f}")
    print(f"  ecart-type : {statistics.pstdev(values):.3f}")
    print(f"  deciles    : {buckets}")
    if max(buckets) > DEGENERATE_SHARE * len(values):
        share = 100 * max(buckets) / len(values)
        print(f"  DEGENERE : {share:.0f} % des tokens dans un seul decile")


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
    axis: list[float] = []
    freshness: list[float] = []
    ranks: list[tuple[int, float]] = []
    mapped = 0

    try:
        for rank, coin_id in enumerate(DEFAULT_COINS[: args.limit], start=1):
            try:
                pairs = await gecko.repos_for(coin_id)
            except Exception as exc:
                print(f"  {coin_id}: mapping indisponible — {exc}", file=sys.stderr)
                continue
            if pairs:
                mapped += 1
            snapshots = [
                snap
                for snap in [
                    await _read_repo(client, owner, repo, now)
                    for owner, repo in pairs[:MAX_REPOS_PER_COIN]
                ]
                if snap is not None
            ]
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
            shown = "—" if value is None else f"{value:.3f}"
            repos = len(pairs)
            print(f"{coin_id:24s} rang={rank:3d} repos={repos:2d} axe={shown}")
            if value is not None:
                axis.append(value)
                ranks.append((rank, value))
            if activity is not None and activity.days_since_push is not None:
                freshness.append(float(activity.days_since_push))
    finally:
        await client.close()
        await gecko.close()

    total = min(args.limit, len(DEFAULT_COINS))
    print(f"\ncouverture mapping : {mapped}/{total} coins avec au moins un depot")
    print(f"couverture axe     : {len(axis)}/{total} tokens mesures")
    if total and mapped / total < 0.6:
        print("SOUS LE CRITERE : la couverture du mapping doit atteindre 60 %")

    _report("axe developer_activity", axis)
    if freshness:
        print(f"\nfraicheur (jours) : mediane {statistics.median(freshness):.0f}")
    print(
        "\nstar_growth n'est pas mesurable ici : il demande deux snapshots "
        "espaces, que ce script ne produit pas. A relever en production apres "
        "un cycle complet de 12 h."
    )

    if len(axis) >= 3 and ranks:
        # Corrélation de rang : si l'axe reproduit le classement par
        # capitalisation, il ne dit rien de neuf.
        order = [v for _, v in sorted(ranks)]
        pairs_total = concordant = 0
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                pairs_total += 1
                concordant += order[i] > order[j]
        tau = (2 * concordant / pairs_total) - 1 if pairs_total else 0.0
        print(f"correlation au rang de capitalisation : tau = {tau:+.2f}")
        if abs(tau) > 0.7:
            print("DEGENERE : l'axe reproduit le classement par capitalisation")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
