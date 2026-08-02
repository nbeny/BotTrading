"""Registre des projets crypto, mapping coin->repo, snapshots GitHub

Trois tables pour l'axe developer_activity.

Le registre est volontairement découplé du mapping : il recense ~8 400 projets
issus des deux awesome-lists, dont la grande majorité n'a aucun ticker, tandis
que coin_repo_map ne contient que les rattachements de confiance effectivement
scorés. Confondre les deux ferait entrer dans l'agrégat des bibliothèques
d'outillage rattachées à un coin par coïncidence de nommage.

Toutes les colonnes de mesure de github_repo_snapshot sont nullables et sans
défaut : un NOT NULL DEFAULT 0 y transformerait « pas encore lu » en « mesuré à
zéro », et le scoring exclut un axe absent mais pénalise un axe mesuré mauvais.

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
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_registry_symbol", "crypto_project_registry", ["symbol"])

    op.create_table(
        "coin_repo_map",
        sa.Column("coin_id", sa.String(128), primary_key=True),
        sa.Column("owner", sa.String(128), primary_key=True),
        sa.Column("repo", sa.String(128), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_repo_map_symbol", "coin_repo_map", ["symbol"])

    op.create_table(
        "github_repo_snapshot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("repo", sa.String(128), nullable=False),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
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
        "ix_github_snapshot_observed_at", "github_repo_snapshot", ["observed_at"]
    )
    op.create_index(
        "ix_github_snapshot_repo",
        "github_repo_snapshot",
        ["owner", "repo", "observed_at"],
    )


def downgrade() -> None:
    op.drop_table("github_repo_snapshot")
    op.drop_table("coin_repo_map")
    op.drop_table("crypto_project_registry")
