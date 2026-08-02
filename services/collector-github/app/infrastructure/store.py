"""Lecture et écriture des snapshots de dépôt.

``stats_from_rows`` est pur et testé séparément : c'est lui qui décide ce que
valent ``stars_prev`` et ``stars_prev_at``, et un décalage d'une ligne y
produirait une croissance d'étoiles fausse mais parfaitement plausible.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, select

from cmi_common.db.models import GithubRepoSnapshot
from cmi_common.db.session import Database

from ..domain.activity import RepoStats

#: Deux lignes suffisent : la courante, et celle qui donne le delta d'étoiles.
_HISTORY = 2


def stats_from_rows(
    owner: str, repo: str, rows: Sequence[dict[str, Any]]
) -> RepoStats | None:
    """Reconstruit un ``RepoStats``, lignes ordonnées du plus récent au plus ancien.

    Toutes les mesures viennent de la ligne courante ; seules les étoiles
    regardent en arrière. Les mélanger daterait les commits d'un cycle entier.

    ``stars_prev`` et ``stars_prev_at`` valent ``None`` s'il n'y a qu'une
    ligne : un delta demande deux observations, et le premier passage sur un
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
        stars_at=current.get("observed_at"),
        stars_prev=previous.get("stars") if previous else None,
        stars_prev_at=previous.get("observed_at") if previous else None,
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
                    "stars": row.stars,
                    "forks": row.forks,
                    "observed_at": row.observed_at,
                    "pushed_at": row.pushed_at,
                    "archived": row.archived,
                    "is_fork": row.is_fork,
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
                    owner=stats.owner,
                    repo=stats.repo,
                    stars=stats.stars,
                    forks=stats.forks,
                    commits_4w=stats.commits_4w,
                    commits_median_52w=stats.commits_median_52w,
                    pr_merged_4w=stats.pr_merged_4w,
                    pr_merged_52w=stats.pr_merged_52w,
                    pushed_at=stats.pushed_at,
                    archived=stats.archived,
                    is_fork=stats.is_fork,
                )
            )
            await session.commit()
