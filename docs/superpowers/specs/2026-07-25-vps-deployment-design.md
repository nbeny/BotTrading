# VPS Deployment + CI/CD — Design Spec

**Date:** 2026-07-25
**Goal:** Deploy the full CMI stack to the Hostinger VPS (`<VPS_HOST>`) behind the
existing shared Traefik, with GitHub Actions building images and auto-deploying on push.

## Target environment (probed 2026-07-25)

- VPS: **7.8 GB RAM, no swap, 2 vCPU**, ~4.6 GB free (claudo-api alone uses 2.1 GB), 57 GB disk free.
- Shared **Traefik** already running (host network), watches Docker provider on the external
  `traefik-public` network, ACME resolver **`letsencrypt`** (HTTP-01), Cloudflare in front,
  CrowdSec bouncer applied globally on the `websecure` entrypoint.
- Claude subscription auth present at `/root/.claude` (used by claudo, uid 1000).

## Decisions

1. **Single public host `crypto.nbeny.fr`** (Cloudflare Universal SSL only covers one subdomain
   level, so `api.crypto.nbeny.fr` would fail TLS at the edge). The frontend proxies REST
   server-side via Next.js rewrites, so no public API subdomain is needed:
   - `crypto.nbeny.fr` → **web-terminal** (Next.js :3000)
   - `crypto.nbeny.fr/ws` → **websocket-gateway** (:8000), `PathPrefix(/ws)` priority 100
   - **api-gateway / control-api**: internal only (no Traefik labels); reached by the Next.js
     server over the internal `cmi` network via `API_GATEWAY_URL` / `CONTROL_API_URL`.
2. **Full pipeline + swap.** Add 8 GB swap. Kafka heap capped at 512 MB, `HAIKU_REPLICAS=1`.
   Observability (prometheus/grafana/otel) behind the Compose `observability` profile, **off by
   default** (−570 MB); `OTEL_TRACING_ENABLED=false` when off so services don't dial a missing collector.
3. **Build in CI → push to GHCR → VPS pulls.** The VPS never builds (2 vCPU / tight RAM would
   OOM on the PyTorch + Next.js builds). Images: `ghcr.io/nbeny/bottrading-<svc>:{latest,<sha>}`.
4. **AI-worker Claude auth via `CLAUDE_CODE_OAUTH_TOKEN`** (long-lived `claude setup-token`),
   not a mounted credentials file — avoids the uid-10001-vs-uid-1000 permission clash with claudo
   and the OAuth-refresh race. Set once in the VPS `.env`.
5. **Live read-plane confirmed complete** (47 contract tests green, all frontend paths covered,
   router mounted). Deploy in full live mode.

## Files

- `docker-compose.vps.yml` — standalone VPS stack (GHCR images, `traefik-public` labels,
  `observability` profile, live env).
- `.env.vps.example` — VPS env template (`cp .env.vps.example .env` on the VPS).
- `.github/workflows/deploy.yml` — CI test → build/push image matrix (15 images) → SSH deploy.
- `scripts/setup-vps.sh` — one-shot: 8 GB swap, `/opt/bottrading`, `traefik-public` network.
- `scripts/deploy-vps.sh` — runs on the VPS: GHCR login, `compose pull`, `compose up -d`.

## CI/CD flow

`push → master`:
1. **test** — install `cmi_common` + api-gateway, run the read-plane pytest suite.
2. **images** (matrix, needs test) — `docker/build-push-action` builds each service and pushes to GHCR.
3. **deploy** (needs images) — rsync `docker-compose.vps.yml` + `scripts/` + `observability/` to
   `/opt/bottrading`, then SSH `deploy-vps.sh` with `TAG=<sha>`. GHCR pull auth via the job's
   `GITHUB_TOKEN` (piped to `docker login` on the VPS). PRs run **test** only.

## Secrets (GitHub Actions)

- `VPS_SSH_KEY` — private half of a dedicated ed25519 deploy key (public half in the VPS
  `authorized_keys`).
- `VPS_HOST` = `<VPS_HOST>`, `VPS_USER` = `root`.

## Manual prerequisites

- Cloudflare DNS: `crypto.nbeny.fr` A → `<VPS_HOST>` (proxied).
- VPS `.env`: `DB_PASSWORD`, `JWT_SECRET`, `CONTROL_ADMIN_PASSWORD`, `CLAUDE_CODE_OAUTH_TOKEN`
  (+ optional API keys, Kraken keys for live trading).

## Known limitations

- AI workers require `CLAUDE_CODE_OAUTH_TOKEN`; without it they restart-loop (rest of the stack
  is unaffected — market/sentiment/read plane still work, but no AI decisions → no autonomous trades).
- Memory is tight; swap absorbs spikes but the box will be slow under concurrent AI-CLI load next
  to claudo. Observability is opt-in to preserve headroom.
- trading-engine defaults to `dry_run` (no Kraken keys needed).
