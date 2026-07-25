#!/usr/bin/env bash
# One-shot VPS preparation for the BotTrading / CMI stack. Idempotent — safe to
# re-run. Run as root on the VPS:
#     bash /opt/bottrading/scripts/setup-vps.sh
#
# Does three things:
#   1. Adds 8 GB of swap (the box has none) so the full stack survives RAM spikes.
#   2. Creates the deploy directory /opt/bottrading.
#   3. Ensures the external `traefik-public` network the shared Traefik watches.
set -euo pipefail

DEPLOY_DIR=/opt/bottrading
SWAPFILE=/swapfile
SWAP_SIZE_GB=8

echo "==> 1/3 swap"
if swapon --show | grep -q .; then
  echo "    swap already active — skipping"
else
  if fallocate -l "${SWAP_SIZE_GB}G" "$SWAPFILE" 2>/dev/null; then :; else
    dd if=/dev/zero of="$SWAPFILE" bs=1M count=$((SWAP_SIZE_GB * 1024))
  fi
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
  swapon "$SWAPFILE"
  grep -q "^$SWAPFILE" /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
  # Favour keeping processes in RAM; only swap under real pressure.
  sysctl -w vm.swappiness=10 >/dev/null
  grep -q "vm.swappiness" /etc/sysctl.conf || echo "vm.swappiness=10" >> /etc/sysctl.conf
  echo "    added ${SWAP_SIZE_GB}G swap"
fi

echo "==> 2/3 deploy dir"
mkdir -p "$DEPLOY_DIR/scripts" "$DEPLOY_DIR/observability"
echo "    $DEPLOY_DIR ready"

echo "==> 3/3 traefik-public network"
if docker network inspect traefik-public >/dev/null 2>&1; then
  echo "    traefik-public already exists — skipping"
else
  docker network create traefik-public
  echo "    created traefik-public"
fi

echo "==> done. Next: copy .env into $DEPLOY_DIR/.env (see .env.vps.example),"
echo "    then a push to master auto-deploys via GitHub Actions."
