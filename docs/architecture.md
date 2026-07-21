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
| `events/`           | Schémas Pydantic typés + union discriminée + `parse_event`   |
| `kafka/`            | `EventProducer`, `EventConsumer` (at-least-once), registre topics |
| `db/`               | Base déclarative, modèles ORM, `Database` (session async)    |
| `cache/`            | `Cache` Redis : JSON cache, rate-limit, locks distribués     |
| `ai/`               | `ClaudeClient` (wrapper Anthropic + stub offline)            |
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

## Frontières & responsabilités

| Service                  | Consomme                              | Produit                    |
| ------------------------ | ------------------------------------- | -------------------------- |
| collector-coingecko      | — (poll HTTP)                         | price, volume              |
| collector-dexscreener    | — (poll HTTP)                         | dex                        |
| collector-cryptocompare  | — (poll HTTP)                         | news                       |
| collector-reddit         | — (poll HTTP)                         | social                     |
| sentiment-service        | news, social                          | sentiment                  |
| ai-worker-haiku          | price, volume, dex, news, social, sentiment | analysis             |
| ai-worker-sonnet         | analysis (escalate=true)              | decision                   |
| decision-engine          | analysis, sentiment                   | decision                   |
| risk-engine              | decision                              | risk.approved (+ rejected) |
| api-gateway              | analysis, decision, risk.approved     | REST + persistance DB      |
```
