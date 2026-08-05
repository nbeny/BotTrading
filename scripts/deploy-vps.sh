#!/usr/bin/env bash
# Runs ON the VPS (invoked over SSH by .github/workflows/deploy.yml). Pulls the
# freshly built GHCR images and (re)starts the stack. Never builds anything.
#
# Expected environment (passed by the workflow):
#   REGISTRY    e.g. ghcr.io/nbeny        (default below)
#   TAG         image tag to deploy       (default: latest)
#   GHCR_USER   GHCR login user           (github.actor)
#   GHCR_TOKEN  GHCR token                (GITHUB_TOKEN, packages:read)
# Optional:
#   COMPOSE_PROFILES=observability   to also start prometheus/grafana/otel.
set -euo pipefail

DEPLOY_DIR=/opt/bottrading
cd "$DEPLOY_DIR"

export REGISTRY="${REGISTRY:-ghcr.io/nbeny}"
export TAG="${TAG:-latest}"

if [ ! -f .env ]; then
  echo "FATAL: $DEPLOY_DIR/.env is missing. Copy .env.vps.example and fill it in." >&2
  exit 1
fi

# GHCR login (images may be private). Token piped via stdin, never echoed.
if [ -n "${GHCR_TOKEN:-}" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-nbeny}" --password-stdin
fi

COMPOSE="docker compose -f docker-compose.vps.yml"

# Le prune de fin de script arrive trop tard quand le disque est deja plein :
# `compose pull` echoue avant de l'atteindre, et son message ne nomme pas le
# disque ("failed to create prepare snapshot dir"), ce qui envoie chercher la
# panne ailleurs. Un deploiement tire ~19 images taguees au SHA du commit, soit
# une quinzaine de Go qui s'ajoutent aux precedentes avant que quoi que ce soit
# ne soit liberé. On fait donc de la place d'abord.
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "==> disque: ${FREE_GB} Go libres avant pull"
if [ "${FREE_GB:-0}" -lt 25 ]; then
  echo "==> moins de 25 Go libres : nettoyage prealable"
  docker builder prune -af >/dev/null 2>&1 || true
  # `until=24h` et pas 48h : ici on est deja contraint, on garde moins de marge
  # de rollback plutot que d'echouer le deploiement.
  docker image prune -af --filter "until=24h" >/dev/null 2>&1 || true
  df -h / | awk 'NR==2 {print "==> disque apres nettoyage: " $4 " libres (" $5 " utilises)"}'
fi

echo "==> pulling images @ ${TAG}"
$COMPOSE pull

echo "==> applying migrations"
$COMPOSE run --rm migrate

echo "==> starting stack"
# Recreating ~15 services at once on 2 vCPU can briefly spike load and make an
# already-healthy Kafka's healthcheck flap, which aborts `up -d`. Retry once
# after a short settle before giving up.
$COMPOSE up -d --remove-orphans || {
  echo "up -d failed (likely a transient health flap under load); settling 30s and retrying once"
  sleep 30
  $COMPOSE up -d --remove-orphans
}

echo "==> pruning images no longer in use"
# `-a`, not just dangling. Every deploy pulls images tagged with the commit SHA,
# so the previous deploy's images stay *tagged* and are therefore never
# dangling: a dangling-only prune reclaimed nothing and 196 images had piled up
# to 71 GB, taking the disk to 96% full with 4.5 GB left. `until=48h` keeps the
# last two days of tags so a rollback still has something to roll back to.
docker image prune -af --filter "until=48h" >/dev/null 2>&1 || true
df -h / | awk 'NR==2 {print "==> disk after prune: " $4 " free (" $5 " used)"}'

echo "==> deployed. Running services:"
$COMPOSE ps
