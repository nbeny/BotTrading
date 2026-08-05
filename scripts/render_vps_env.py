#!/usr/bin/env python3
"""Rend le /opt/bottrading/.env du VPS depuis l'environnement du runner.

Appele par .github/workflows/deploy.yml. Les valeurs arrivent par le bloc `env:`
de l'etape, donc par os.environ : aucune n'est interpolee dans du shell, ou un
guillemet ou un dollar suffirait a casser le fichier ou a fuiter dans les logs.

Ecrit exactement les cles que porte le .env de production. Les autres variables
lues par docker-compose.vps.yml gardent leur defaut inline.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# (cle ecrite dans le .env, variable d'environnement du runner qui la porte).
#
# Les deux noms different pour tout ce qui touche au collector GitHub : GitHub
# refuse tout secret ET toute variable dont le nom commence par GITHUB_ (HTTP 422
# "Variable names must not start with GITHUB_"), donc ils voyagent sous
# GH_COLLECTOR_* et retrouvent leur nom ici.
KEYS: list[tuple[str, str]] = [
    ("DB_PASSWORD", "DB_PASSWORD"),
    ("JWT_SECRET", "JWT_SECRET"),
    ("CONTROL_ADMIN_USER", "CONTROL_ADMIN_USER"),
    ("CONTROL_ADMIN_PASSWORD", "CONTROL_ADMIN_PASSWORD"),
    ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ("REGISTRY", "REGISTRY"),
    ("TAG", "TAG"),
    ("LOG_LEVEL", "LOG_LEVEL"),
    ("ENVIRONMENT", "ENVIRONMENT"),
    ("DB_USER", "DB_USER"),
    ("DB_NAME", "DB_NAME"),
    ("KAFKA_HEAP_OPTS", "KAFKA_HEAP_OPTS"),
    ("HAIKU_REPLICAS", "HAIKU_REPLICAS"),
    ("ANTHROPIC_CLI_CONCURRENCY", "ANTHROPIC_CLI_CONCURRENCY"),
    ("OTEL_TRACING_ENABLED", "OTEL_TRACING_ENABLED"),
    ("TRADING_MODE", "TRADING_MODE"),
    ("NEWSDATA_API_KEY", "NEWSDATA_API_KEY"),
    ("NEYNAR_API_KEY", "NEYNAR_API_KEY"),
    ("YOUTUBE_API_KEY", "YOUTUBE_API_KEY"),
    ("BLUESKY_APP_PASSWORD", "BLUESKY_APP_PASSWORD"),
    ("BLUESKY_IDENTIFIER", "BLUESKY_IDENTIFIER"),
    ("TURNSTILE_SECRET_KEY", "TURNSTILE_SECRET_KEY"),
    ("TELEGRAM_API_ID", "TELEGRAM_API_ID"),
    ("TELEGRAM_API_HASH", "TELEGRAM_API_HASH"),
    ("TELEGRAM_SESSION", "TELEGRAM_SESSION"),
    ("GITHUB_TOKEN", "GH_COLLECTOR_TOKEN"),
    ("GITHUB_POLL_INTERVAL", "GH_COLLECTOR_POLL_INTERVAL"),
    ("GITHUB_MAX_REFRESH_PER_CYCLE", "GH_COLLECTOR_MAX_REFRESH_PER_CYCLE"),
    ("GITHUB_UNIVERSE_SIZE", "GH_COLLECTOR_UNIVERSE_SIZE"),
    ("GITHUB_LISTS_REFRESH_HOURS", "GH_COLLECTOR_LISTS_REFRESH_HOURS"),
]

# Sans elles la stack demarre mais ne fait rien d'utile : mieux vaut echouer ici,
# ou le message est lisible, que sur le VPS ou il faudrait lire des logs docker.
REQUIRED = {
    "DB_PASSWORD",
    "JWT_SECRET",
    "CONTROL_ADMIN_USER",
    "CONTROL_ADMIN_PASSWORD",
    "REGISTRY",
    "TAG",
}


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".env.render")

    missing = [
        dest for dest, src in KEYS if dest in REQUIRED and not os.environ.get(src)
    ]
    if missing:
        print(
            f"FATAL: variables requises absentes ou vides: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    lines = [
        "# Genere par .github/workflows/deploy.yml -- ne pas editer a la main.",
        "",
    ]
    empty: list[str] = []
    for dest, src in KEYS:
        value = os.environ.get(src, "")
        if not value:
            empty.append(dest)
        # Pas de guillemets : docker-compose les prendrait pour une partie de la
        # valeur. Un retour ligne casserait le format, on le refuse plutot que de
        # produire un fichier silencieusement tronque.
        if "\n" in value or "\r" in value:
            print(f"FATAL: {dest} contient un retour ligne", file=sys.stderr)
            return 1
        lines.append(f"{dest}={value}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    out.chmod(0o600)

    # Des noms, jamais des valeurs.
    print(f"rendu {out} : {len(KEYS)} cles")
    if empty:
        print(f"cles vides (source desactivee) : {', '.join(empty)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
