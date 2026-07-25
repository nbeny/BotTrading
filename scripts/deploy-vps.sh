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

echo "==> pulling images @ ${TAG}"
$COMPOSE pull

echo "==> applying migrations"
$COMPOSE run --rm migrate

echo "==> starting stack"
$COMPOSE up -d --remove-orphans

echo "==> pruning dangling images"
docker image prune -f >/dev/null 2>&1 || true

echo "==> deployed. Running services:"
$COMPOSE ps
