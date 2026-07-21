# Crypto Market Intelligence (CMI) Platform

> Plateforme **event-driven** de *Market Intelligence* crypto : collecte temps réel,
> analyse d'opportunités (déterministe + IA), scoring de risque et production
> d'événements exploitables par un moteur de trading autonome.

Ceci n'est **pas** du high-frequency trading. C'est un système autonome de détection :
nouveaux tokens prometteurs, mouvements de volume, tendances sociales, impact des news,
opportunités et risques associés.

---

## 1. Vue d'ensemble de l'architecture

```
                          ┌──────────────────────────────────────────────┐
   DATA SOURCES           │                DATA COLLECTORS               │
 ─────────────────        │  (microservices indépendants, stateless)     │
  CoinGecko API   ───────►│  collector-coingecko  → market.price.events  │
  DexScreener API ───────►│  collector-dexscreener→ market.dex.events    │
  CryptoCompare   ───────►│  collector-cryptocompare→ market.news.events │
  Reddit API      ───────►│  collector-reddit     → market.social.events │
                          └───────────────────────┬──────────────────────┘
                                                   │
                                          ┌────────▼────────┐
                                          │   KAFKA BUS     │  (backbone événementiel)
                                          └────────┬────────┘
             ┌─────────────────────────────────────┼─────────────────────────────────┐
             │                                      │                                 │
   ┌─────────▼─────────┐              ┌─────────────▼────────────┐        ┌───────────▼──────────┐
   │ sentiment-service │              │   ai-worker-haiku        │        │   decision-engine    │
   │ HuggingFace (L1)  │              │  triage / corrélation /  │        │  scoring déterministe│
   │ Crypto/FinBERT    │              │  scoring d'opportunité   │        │  (momentum, volume,  │
   │→ market.sentiment │              │→ market.analysis.events  │        │   liq, sentiment...) │
   └─────────┬─────────┘              └─────────────┬────────────┘        └───────────┬──────────┘
             │                                      │                                 │
             └──────────────►  market.sentiment.events / market.analysis.events ◄─────┘
                                                   │
                                       ┌───────────▼───────────┐
                                       │   ai-worker-sonnet    │  (analyste senior, sur signaux forts)
                                       │  validation multi-sig │
                                       │→ decision.events      │
                                       └───────────┬───────────┘
                                                   │
                                       ┌───────────▼───────────┐
                                       │      risk-engine      │  (indépendant)
                                       │  SL / TP / exposition │
                                       │  blacklist            │
                                       │→ risk.approved.events │
                                       └───────────┬───────────┘
                                                   │
                                          ► consommé par le moteur de trading
```

Voir les diagrammes de flux détaillés dans [`docs/flows.md`](docs/flows.md),
la stratégie de scaling dans [`docs/scaling.md`](docs/scaling.md),
et l'architecture logicielle (clean architecture) dans [`docs/architecture.md`](docs/architecture.md).

---

## 2. Stack technique

| Domaine            | Technologie                                             |
| ------------------ | ------------------------------------------------------- |
| Langage            | Python 3.12                                             |
| API / services     | FastAPI + Uvicorn                                       |
| Validation         | Pydantic v2                                             |
| ORM                | SQLAlchemy 2.0 (async)                                  |
| Concurrence        | AsyncIO                                                 |
| HTTP client        | httpx (async)                                           |
| Event bus          | Kafka (mode KRaft) + aiokafka                           |
| Base de données    | PostgreSQL 16 + TimescaleDB (hypertables séries temps)  |
| Cache / locks      | Redis 7                                                 |
| Sentiment L1       | HuggingFace Transformers (CryptoBERT / FinBERT / RoBERTa) |
| IA L2/L3           | Claude Haiku (triage) + Claude Sonnet (analyste senior) |
| Observabilité      | Prometheus, Grafana, OpenTelemetry, Sentry              |
| Reverse proxy      | Traefik (HTTPS / Let's Encrypt / labels Docker)         |
| Migrations         | Alembic                                                 |
| Qualité            | pytest, coverage, mypy, ruff, black, pre-commit         |
| Déploiement        | Docker Compose                                          |

---

## 3. Topics Kafka

| Topic                     | Producteur              | Consommateur(s)                         |
| ------------------------- | ----------------------- | --------------------------------------- |
| `market.price.events`     | collector-coingecko     | decision-engine, ai-worker-haiku        |
| `market.volume.events`    | collector-coingecko/dex | decision-engine                         |
| `market.dex.events`       | collector-dexscreener   | decision-engine, ai-worker-haiku        |
| `market.news.events`      | collector-cryptocompare | sentiment-service, ai-worker-haiku      |
| `market.social.events`    | collector-reddit        | sentiment-service, ai-worker-haiku      |
| `market.sentiment.events` | sentiment-service       | decision-engine, ai-worker-haiku        |
| `market.analysis.events`  | ai-worker-haiku         | ai-worker-sonnet, decision-engine       |
| `decision.events`         | ai-worker-sonnet, decision-engine | risk-engine                   |
| `risk.approved.events`    | risk-engine             | trading engine (externe)                |

Tous les événements sont typés avec des schémas **Pydantic** dans
[`libs/cmi_common/cmi_common/events`](libs/cmi_common/cmi_common/events).

---

## 4. Structure des dossiers

```
BotTrading/
├── docker-compose.yml            # orchestration complète
├── .env.example                  # variables d'environnement
├── pyproject.toml                # tooling (ruff, black, mypy, pytest)
├── .pre-commit-config.yaml
├── Makefile
├── docs/                         # architecture, flux, scaling
├── traefik/                      # config statique + dynamique Traefik
├── libs/
│   └── cmi_common/               # bibliothèque partagée (events, kafka, db, cache, obs)
├── services/
│   ├── collector-coingecko/
│   ├── collector-dexscreener/
│   ├── collector-cryptocompare/
│   ├── collector-reddit/
│   ├── sentiment-service/
│   ├── ai-worker-haiku/
│   ├── ai-worker-sonnet/
│   ├── decision-engine/
│   ├── risk-engine/
│   └── api-gateway/
├── migrations/                   # Alembic
└── tests/                        # tests transverses
```

Chaque service suit la **clean architecture** : `domain/` (entités, règles pures),
`application/` (use-cases, orchestration), `infrastructure/` (Kafka, HTTP, DB, cache),
`api/` (FastAPI). L'injection de dépendances passe par un conteneur `deps.py` par service.

---

## 5. Démarrage rapide

```bash
cp .env.example .env            # renseigner les clés API + secrets
make up                         # build + démarrage docker compose
make migrate                    # applique les migrations Alembic
make logs                       # suit les logs
```

Endpoints (via Traefik) :

- `https://app.cmi.localhost`            → **web-terminal** (interface Next.js de supervision/contrôle)
- `https://api.cmi.localhost`            → api-gateway
- `wss://ws.cmi.localhost/ws`            → websocket-gateway (flux Kafka → WebSocket temps réel)
- `https://traefik.cmi.localhost`        → dashboard Traefik
- `https://grafana.cmi.localhost`        → Grafana
- `https://prometheus.cmi.localhost`     → Prometheus

Le terminal web (`frontend/`) tourne aussi en **mode démo autonome** sans backend :
`cd frontend && npm install && npm run dev` → http://localhost:3000. Voir
[`frontend/README.md`](frontend/README.md).

Chaque service expose `/health` (liveness/readiness) et `/metrics` (Prometheus).

---

## 6. Qualité

```bash
make lint      # ruff + black --check + mypy
make format    # ruff --fix + black
make test      # pytest + coverage
pre-commit install
```

---

## 7. Livrables

| # | Livrable                    | Emplacement                                   |
| - | --------------------------- | --------------------------------------------- |
| 1 | Architecture complète       | ce README + `docs/architecture.md`            |
| 2 | Diagrammes de flux          | `docs/flows.md`                               |
| 3 | Structure des dossiers      | §4 + arborescence du repo                      |
| 4 | Modèles Pydantic            | `libs/cmi_common/cmi_common/events/`          |
| 5 | Topics Kafka                | `libs/cmi_common/cmi_common/kafka/topics.py`  |
| 6 | Exemples d'événements       | `docs/flows.md` + `tests/`                    |
| 7 | docker-compose.yml          | racine                                        |
| 8 | Configuration Traefik       | `traefik/`                                     |
| 9 | Premiers microservices      | `services/`                                    |
| 10| Stratégie de scaling        | `docs/scaling.md`                             |
```
