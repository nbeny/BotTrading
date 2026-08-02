# Crypto Market Intelligence (CMI) Platform

> Plateforme **event-driven** de *Market Intelligence* crypto **et de trading autonome** :
> collecte temps réel, analyse d'opportunités (déterministe + IA), scoring de risque,
> **exécution guardée sur Kraken Futures** et **terminal web de supervision/contrôle**.

Ceci n'est **pas** du high-frequency trading. C'est un système autonome de détection
(nouveaux tokens, mouvements de volume, tendances sociales, impact des news) qui produit
des opportunités/risques, puis les **exécute sous garde-fous** avec un opérateur humain
dans la boucle.

Ce README couvre le **pipeline de collecte/analyse**. L'architecture runtime détaillée du
**plan de contrôle/exécution** (control-api, trading-engine, websocket-gateway, frontend)
est documentée dans [`CLAUDE.md`](CLAUDE.md).

---

## 1. Vue d'ensemble de l'architecture

```
 ┌─────────────────────── INGESTION ───────────────────────┐
 CoinGecko   ─► collector-coingecko  ─► market.price/volume.events ─┐
 DexScreener ─► collector-dexscreener ─► market.dex.events ─────────┤ (Kafka)
                                                                    │
 Bluesky/Reddit/Mastodon/4chan/                                     │
 Farcaster/YouTube/Lens  ─► collector-social ─┐                     │
 CryptoCompare/RSS/GDELT/NewsData ─► collector-news ─┤              │
                                                     ▼              │
                                          Postgres  raw_content     │
                                                     │              │
                                          sentiment-service (HF L1) │
                                          scores le DB, upsert       │
                                          content_sentiment_agg      │
                                                     ▼              │
                                          market.sentiment.events ──┤
 └──────────────────────────────────────────────────────────────────┘
                                                     │
                    ┌────────────────────────────────┼───────────────────────┐
                    ▼                                 ▼                        ▼
             ai-worker-haiku                   decision-engine          (feature store)
        (triage/corrélation, score)      (scoring déterministe)
                    │ escalate=true                  │
                    ▼                                 │
             ai-worker-sonnet ─► decision.events ◄────┘
             (analyste senior)          │
                                        ▼
                                    risk-engine ─► risk.approved.events
                                                          │
 ┌──────────────── PLAN DE CONTRÔLE / EXÉCUTION ──────────┼───────────────────┐
 │                                                        ▼                    │
 │  control-api ─► control.commands ─►  trading-engine  ─► execution.events    │
 │  (JWT, écritures)                    (Kraken Futures,        │              │
 │        ▲                              dry_run/demo/live)     │              │
 │        │ REST /trading/*                     │ Redis trading:*│             │
 │   web-terminal (Next.js)  ◄── WS ── websocket-gateway ◄──────┴─ 10 topics   │
 │        │ REST /portfolio,/market (lecture)                                  │
 │        └──────────────────────► api-gateway (READ-ONLY, Kafka→Postgres)     │
 └────────────────────────────────────────────────────────────────────────────┘
```

Diagrammes de flux détaillés : [`docs/flows.md`](docs/flows.md) · stratégie de scaling :
[`docs/scaling.md`](docs/scaling.md) · clean architecture : [`docs/architecture.md`](docs/architecture.md).

---

## 2. Ingestion DB-sourced (fan-out par plateforme)

Les **collectors sociaux/news** ne publient plus sur Kafka. Chaque plateforme tourne dans
sa **propre boucle de poll adaptative** (`AdaptivePollLoop`) qui s'auto-throttle sur sa
limite de débit (apprise depuis les headers de l'API, token-bucket Redis), déduplique par
`(source, external_id)`, et persiste des items normalisés dans Postgres **`raw_content`**
(hypertable Timescale). Pas de cascade/failover : une source en pause reprend seule, sans
impacter les autres.

`sentiment-service` ne consomme plus Kafka : il **scanne `raw_content`** pour les lignes non
scorées (`scored_at IS NULL`), les score en batch (HuggingFace CryptoBERT, fallback lexical),
réécrit le score sur la ligne, **upsert `content_sentiment_agg`** (agrégat par symbole/kind/
fenêtre horaire) et publie un `SentimentEvent` par (item × symbole détecté).

| Collecteur          | Providers                                                     | Clé requise                        |
| ------------------- | ------------------------------------------------------------- | ---------------------------------- |
| **collector-social** | `bluesky`, `reddit`, `mastodon`, `fourchan`, `lens`          | keyless                            |
|                     | `neynar` (Farcaster), `youtube`                               | `NEYNAR_API_KEY`, `YOUTUBE_API_KEY` |
|                     | `telegram` (chaînes signal/annonces)                          | `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` + `TELEGRAM_SESSION` |
| **collector-news**  | `rss`, `gdelt`                                                | keyless                            |
|                     | `cryptocompare`                                              | `CRYPTOCOMPARE_API_KEY` (optionnelle) |
|                     | `newsdata`                                                   | `NEWSDATA_API_KEY`                 |

Les sources key-gated ne s'activent que si leur variable d'environnement est renseignée.
Telegram passe par **MTProto avec une session utilisateur** (un bot ne voit que les chaînes
qu'il administre) : la session se génère une fois en local avec
`python scripts/telegram_session.py`, puis se colle dans `.env` — le conteneur ne peut pas
répondre à un code de connexion. La liste de chaînes est relue à chaque cycle depuis la clé
Redis `collectors:runtime`, donc éditable depuis le terminal sans redéploiement ;
`TELEGRAM_CHANNELS` ne fait que l'amorcer au premier démarrage, à défaut de quoi c'est la
graine `cmi_common.sources.runtime::TELEGRAM_SEED_CHANNELS` qui sert.
Le framework partagé est dans [`libs/cmi_common/cmi_common/sources/`](libs/cmi_common/cmi_common/sources)
(`provider.py`, `raw.py`, `loop.py`, `repository.py`).

---

## 3. Stack technique

| Domaine            | Technologie                                             |
| ------------------ | ------------------------------------------------------- |
| Langage            | Python 3.12                                             |
| API / services     | FastAPI + Uvicorn                                       |
| Validation         | Pydantic v2                                             |
| ORM                | SQLAlchemy 2.0 (async)                                  |
| Event bus          | Kafka (mode KRaft) + aiokafka                           |
| Base de données    | PostgreSQL 16 + TimescaleDB (hypertables)               |
| Cache / locks / runtime | Redis 7                                            |
| Sentiment L1       | HuggingFace Transformers (CryptoBERT / FinBERT / RoBERTa) |
| IA L2/L3           | Claude Haiku (triage) + Claude Sonnet (analyste senior) |
| Transport IA       | CLI `claude -p` (abonnement) **ou** SDK Anthropic (`api`) |
| Exécution          | Kraken **Futures** (dry_run / demo / live)              |
| Frontend           | Next.js 15 + React 19 + MUI v6 (terminal de contrôle)   |
| Observabilité      | Prometheus, Grafana, OpenTelemetry, Sentry              |
| Reverse proxy      | Traefik v3 (HTTPS / Let's Encrypt / labels Docker)      |
| Migrations         | Alembic                                                 |
| Qualité            | pytest, coverage, mypy, ruff, black, pre-commit         |
| Déploiement        | Docker Compose                                          |

---

## 4. Topics Kafka

| Topic                     | Producteur                        | Consommateur(s)                          |
| ------------------------- | --------------------------------- | ---------------------------------------- |
| `market.price.events`     | collector-coingecko               | decision-engine, ai-worker-haiku         |
| `market.volume.events`    | collector-coingecko               | decision-engine, ai-worker-haiku         |
| `market.dex.events`       | collector-dexscreener             | decision-engine, ai-worker-haiku         |
| `market.sentiment.events` | sentiment-service                 | decision-engine, ai-worker-haiku         |
| `market.analysis.events`  | ai-worker-haiku                   | ai-worker-sonnet, decision-engine, api-gateway |
| `decision.events`         | ai-worker-sonnet, decision-engine | risk-engine, api-gateway                 |
| `risk.approved.events`    | risk-engine                       | trading-engine                           |
| `execution.events`        | trading-engine                    | api-gateway, websocket-gateway           |
| `control.commands`        | control-api                       | trading-engine                           |
| `market.news.events` / `market.social.events` | *(réservés)*  | *legacy — l'ingestion sociale/news passe désormais par Postgres `raw_content`* |

Tous les événements sont typés avec des schémas **Pydantic v2** dans
[`libs/cmi_common/cmi_common/events`](libs/cmi_common/cmi_common/events) ; noms canoniques et
partitions dans [`kafka/topics.py`](libs/cmi_common/cmi_common/kafka/topics.py).

---

## 5. Plan de contrôle / exécution

Trois surfaces backend sont posées devant le frontend. **Ne pas mélanger lecture et écriture :**

| Service              | Hôte Traefik / port          | Rôle                                   |
| -------------------- | ---------------------------- | -------------------------------------- |
| **api-gateway**      | `api.cmi.localhost`          | REST **lecture seule** ; persiste Kafka→Postgres (Signal/Decision/Trade). Endpoints `GET /api/v1/{opportunities,decisions,trades}` |
| **control-api**      | `control.cmi.localhost` (dev `:8001`) | **plan d'écriture/contrôle** + `/auth/login` (JWT). Publie `ControlCommandEvent` sur `control.commands`, lit Redis `trading:*` |
| **trading-engine**   | *(background)*               | **cœur d'exécution**, trade Kraken Futures. Consomme `risk.approved.events` + `control.commands`, produit `execution.events`, RW Redis `trading:*` |
| **websocket-gateway**| `ws.cmi.localhost` (dev `:8080`) | diffuse 10 topics marché/décision/exécution → WS `/ws?token=` |

- **control-api possède toute action bot** : `/trading/{mode,kill,auto,caps,orders}`,
  `/trading/positions/{id}/{close,sltp}`, `/trading/opportunities/{id}/{approve,reject}` —
  chaque endpoint publie un `ControlCommandEvent`. Il n'écrit rien directement : le
  trading-engine applique la commande et mute Redis (human-in-the-loop).
- **trading-engine** — modes `dry_run` (défaut compose) / `demo` / `live`. Garde-fous :
  `MAX_ORDER_USD`, `MAX_LEVERAGE`, `MAX_ORDERS_PER_HOUR`. État runtime dans Redis
  `trading:runtime` / `trading:positions*` / `trading:pending*`.
- **workers IA** — transport `cli` par défaut : chaque appel spawn `claude -p` sous votre
  abonnement OAuth (credentials montés en lecture seule dans le conteneur), au lieu de
  facturer au token via le SDK Anthropic. `ANTHROPIC_TRANSPORT=api` bascule sur le SDK.

---

## 6. Structure des dossiers

```
BotTrading/
├── docker-compose.yml            # orchestration complète
├── docker/                       # Dockerfile, Dockerfile.ml (sentiment), Dockerfile.ai (workers)
├── .env.example                  # variables d'environnement
├── Makefile · pyproject.toml · .pre-commit-config.yaml
├── docs/                         # architecture, flux, scaling
├── traefik/ · observability/     # config Traefik ; Prometheus/Grafana/OTel
├── scripts/                      # create-topics.sh, etc.
├── libs/cmi_common/              # lib partagée (events, kafka, db, cache, ai, sources, auth, state)
├── services/
│   ├── collector-coingecko/ · collector-dexscreener/
│   ├── collector-social/ · collector-news/       # ingestion DB-sourced → raw_content
│   ├── sentiment-service/                         # score raw_content, publie SentimentEvent
│   ├── ai-worker-haiku/ · ai-worker-sonnet/       # transport CLI Claude
│   ├── decision-engine/ · risk-engine/
│   ├── trading-engine/                            # exécution Kraken Futures
│   ├── api-gateway/                               # REST lecture seule
│   ├── control-api/                               # plan de contrôle (écriture)
│   └── websocket-gateway/                         # Kafka → WebSocket
├── frontend/                     # terminal web Next.js (voir frontend/README.md)
├── migrations/                   # Alembic
└── tests/                        # tests transverses
```

Chaque service suit la **clean architecture** : `domain/` (règles pures), `application/`
(use-cases), `infrastructure/` (Kafka/HTTP/DB/cache), `api/` (FastAPI). DI via `deps.py`.

---

## 7. Démarrage rapide

```bash
cp .env.example .env            # renseigner clés API, secrets, chemins CLAUDE_DIR/CLAUDE_CONFIG
make up                         # build + démarrage docker compose (stack complète)
make migrate                    # applique les migrations Alembic
make logs                       # suit les logs
```

Endpoints (via Traefik) :

- `https://app.cmi.localhost`        → **web-terminal** (Next.js, supervision/contrôle)
- `https://api.cmi.localhost`        → api-gateway (lecture seule)
- `https://control.cmi.localhost`    → control-api (auth + `/trading/*`), dev direct `:8001`
- `wss://ws.cmi.localhost/ws`        → websocket-gateway (Kafka → WS), dev direct `:8080`
- `https://traefik.cmi.localhost`    → dashboard Traefik
- `https://grafana.cmi.localhost` · `https://prometheus.cmi.localhost`

Le terminal web tourne aussi en **mode démo autonome** sans backend (`NEXT_PUBLIC_USE_MOCK=1`,
défaut) : `cd frontend && npm install && npm run dev` → http://localhost:3000. Voir
[`frontend/README.md`](frontend/README.md).

Chaque service expose `/health` (liveness/readiness) et `/metrics` (Prometheus).

---

## 8. Qualité

```bash
make lint      # ruff + black --check + mypy
make format    # ruff --fix + black
make test      # pytest + coverage
pre-commit install
```

---

## 9. Livrables

| # | Livrable                    | Emplacement                                   |
| - | --------------------------- | --------------------------------------------- |
| 1 | Architecture runtime        | `CLAUDE.md` + `docs/architecture.md`          |
| 2 | Diagrammes de flux          | `docs/flows.md`                               |
| 3 | Ingestion DB-sourced        | §2 + `libs/cmi_common/cmi_common/sources/`    |
| 4 | Modèles Pydantic            | `libs/cmi_common/cmi_common/events/`          |
| 5 | Topics Kafka                | `libs/cmi_common/cmi_common/kafka/topics.py`  |
| 6 | Exemples d'événements       | `docs/flows.md` + `tests/`                    |
| 7 | Plan de contrôle/exécution  | §5 + `services/{control-api,trading-engine,websocket-gateway}` |
| 8 | docker-compose + Traefik    | racine + `traefik/`                           |
| 9 | Stratégie de scaling        | `docs/scaling.md`                             |
| 10| Terminal web                | `frontend/` + `frontend/README.md`            |
```
