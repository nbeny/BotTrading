# Architecture logicielle

## Principes

La plateforme est **event-driven** et **microservices**. Chaque service :

- est **indépendant** (déployable, scalable et faillible isolément) ;
- communique **uniquement** via Kafka (aucun appel synchrone inter-services) ;
- expose `/health` et `/metrics` ;
- suit la **clean architecture**.

## Clean architecture par service

```
service/app/
├── domain/          # entités + règles métier PURES (aucune I/O, testables seules)
│   └── mapper.py, scoring.py, rules.py ...
├── application/     # use-cases : orchestrent domain + ports (collector, engine, worker)
├── infrastructure/  # adaptateurs concrets : httpx client, Kafka, DB, Redis
└── main.py          # composition root : injection de dépendances + wiring FastAPI
```

Règle de dépendance : `main → application → domain`, `infrastructure → domain`.
Le **domain ne dépend de rien** (ni Kafka, ni httpx, ni DB). Cela rend la logique
de scoring / risque / mapping testable sans conteneur.

### Injection de dépendances

Le *composition root* est `main.py`. Il construit les adaptateurs concrets
(`EventProducer`, `Cache`, `Database`, `ClaudeClient`, clients HTTP), les injecte
dans les use-cases, et gère leur cycle de vie via le `lifespan` FastAPI
(`on_startup` / `on_shutdown`). Aucun singleton global n'est instancié dans le
domain ou l'application.

## Bibliothèque partagée `cmi_common`

Mutualise ce qui doit rester cohérent entre services :

| Module              | Rôle                                                         |
| ------------------- | ------------------------------------------------------------ |
| `events/`           | Schémas Pydantic typés + union discriminée + `parse_event` (market, sentiment, analysis, decision, risk, control, execution) |
| `kafka/`            | `EventProducer`, `EventConsumer` (at-least-once), registre topics |
| `db/`               | Base déclarative, modèles ORM (`raw_content`, `content_sentiment_agg`, Signal/Decision/Trade), `Database` (session async) |
| `cache/`            | `Cache` Redis : JSON cache, rate-limit token-bucket, locks distribués |
| `ai/`               | `ClaudeClient` : transport `CliTransport` (`claude -p`, abonnement), `ApiTransport` (SDK), `StubTransport` (offline) |
| `sources/`          | Framework d'ingestion : `Provider`, `RawItem`, `AdaptivePollLoop`, `ContentRepository` |
| `auth.py` / `state.py` | Helpers JWT (control-api, websocket-gateway) ; `StateReader` Redis `trading:*` |
| `observability/`    | métriques Prometheus, tracing OTel, Sentry                   |
| `app.py`            | Factory FastAPI (`/health`, `/metrics`, lifespan)            |
| `config.py`         | Settings 12-factor (Pydantic Settings)                       |

## Garanties de livraison

- **Producteurs** : idempotents (`enable_idempotence=true`, `acks=all`), clé de
  partition = symbole → ordre garanti par token.
- **Consommateurs** : commit **manuel** après traitement réussi →
  *at-least-once*. Les handlers doivent être **idempotents** (utiliser
  `event_id` / `correlation_id` comme clé de déduplication).
- **Traçabilité** : `correlation_id` propagé du collector jusqu'au
  `RiskApprovedEvent`, permettant de reconstituer toute la chaîne décisionnelle.

## Ingestion DB-sourced (sociale / news)

Les collectors `social` et `news` ne publient **plus** sur Kafka. Chaque plateforme tourne
dans sa propre `AdaptivePollLoop` (poll HTTP indépendant, token-bucket Redis, backoff sur
`RateLimitedError`, dédup `(source, external_id)`) et persiste des `RawItem` normalisés dans
Postgres **`raw_content`**. Pas de cascade/failover : les boucles sont isolées, une source
throttlée n'affecte pas les autres. `sentiment-service` **scanne `raw_content`** (lignes
`scored_at IS NULL`), score en batch, réécrit la ligne, **upsert `content_sentiment_agg`** et
publie `SentimentEvent`. (`CircuitBreaker` existe dans `sources/cascade.py` mais n'est pas
encore câblé — réservé à la coordination multi-réplicas.)

## Frontières & responsabilités

| Service                  | Consomme                              | Produit / effet             |
| ------------------------ | ------------------------------------- | --------------------------- |
| collector-coingecko      | — (poll HTTP)                         | price, volume               |
| collector-dexscreener    | — (poll HTTP)                         | dex                         |
| collector-social         | — (poll HTTP par plateforme)          | → Postgres `raw_content`    |
| collector-news           | — (poll HTTP par plateforme)          | → Postgres `raw_content`    |
| sentiment-service        | Postgres `raw_content` (non scoré)    | sentiment (+ `content_sentiment_agg`) |
| ai-worker-haiku          | price, volume, dex, sentiment         | analysis                    |
| ai-worker-sonnet         | analysis (escalate=true)              | decision                    |
| decision-engine          | analysis, sentiment                   | decision                    |
| risk-engine              | decision                              | risk.approved (+ rejected)  |
| api-gateway              | analysis, decision, risk.approved, execution | REST lecture seule + persistance DB |
| control-api              | — (REST JWT + lecture Redis)          | control.commands            |
| trading-engine           | risk.approved, control.commands       | execution (Kraken Futures) + RW Redis `trading:*` |
| websocket-gateway        | 10 topics (market/decision/exec)      | diffusion WebSocket `/ws`   |

## Plan de contrôle / exécution

- **api-gateway** n'écrit jamais : c'est un persister Kafka→Postgres avec des endpoints `GET`.
- **control-api** possède toute action bot (mode, kill-switch, auto-trading, caps, ordres,
  positions, opportunités). Il publie un `ControlCommandEvent` sur `control.commands` et lit
  l'état runtime dans Redis — il ne mute rien directement (human-in-the-loop).
- **trading-engine** est le seul writer de `trading:runtime`. Il applique les commandes
  opérateur et les `RiskApprovedEvent` autonomes, sous garde-fous (`MAX_ORDER_USD`,
  `MAX_LEVERAGE`, `MAX_ORDERS_PER_HOUR`), en modes `dry_run`/`demo`/`live`.
- **workers IA** : transport `cli` (`claude -p`, abonnement OAuth) par défaut, `api` (SDK) en
  repli. Le sous-processus CLI exclut `ANTHROPIC_API_KEY` de son environnement.
```
