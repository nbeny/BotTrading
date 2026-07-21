# Design — Service `trading-engine` (exécution Kraken Futures)

- **Date** : 2026-07-21
- **Statut** : conception validée (brainstorming) — prêt pour plan d'implémentation
- **Auteur** : Claude Code + utilisateur

## 1. Contexte & problème

Le pipeline actuel (collectors → feature engineering → intelligence engine → sentiment
NLP → scoring/decision-engine → risk-engine) produit un `RiskApprovedEvent` sur le topic
Kafka `risk.approved.events`. **Aucun consommateur n'exécute réellement les trades** :
l'événement est seulement persisté (table `trades`, statut `approved`) et diffusé en
WebSocket. Le « moteur de trading » est aujourd'hui décrit dans le code comme un système
externe non implémenté.

Objectif : créer le maillon manquant — un microservice `trading-engine` qui consomme
`risk.approved.events` et exécute les ordres sur **Kraken Futures**, puis reboucle l'état
(base de données + events Kafka + exposition Redis).

## 2. Périmètre

**Inclus (V1)** :
- Consommation de `risk.approved.events` et exécution sur Kraken Futures.
- Trois modes d'exécution : `dry_run` (log seul), `demo` (testnet Kraken), `live` (prod).
- Ordre d'entrée limit avec fallback market ; SL/TP en ordres reduce-only côté Kraken.
- Garde-fous propres à l'engine : kill-switch global, plafond notional/ordre, levier max
  forcé, limite d'ordres/heure.
- Boucle de retour : MAJ statut trade en DB, `ExecutionEvent` sur Kafka, décrément de
  l'exposition à la fermeture, réconciliation au démarrage.
- Détection de fermeture de position par **polling** Kraken.

**Exclu (hors V1, prévu plus tard)** :
- Feed WebSocket Kraken (`fills`) pour la détection de fill/close en temps réel — phase 2.
  L'interface de `reconcile.py` est conçue pour l'accueillir sans refonte.
- Trailing stops, ordres partiels/scale-in, gestion multi-comptes.
- Contrôle du kill-switch depuis le frontend (l'infra Redis est posée ; l'UI viendra après).

## 3. Décisions clés (validées)

| Sujet | Décision |
|-------|----------|
| Marché | **Kraken Futures** (perpétuels, LONG/SHORT natif, levier) |
| Modes | **dry_run / demo / live**, défaut `dry_run` |
| Entrée | **Limit à `entry_price` avec fallback market** après timeout |
| SL / TP | **Ordres reduce-only côté Kraken** (déclenchés même si le service est down) |
| Client API | **Client REST maison httpx** signant les requêtes Kraken Futures |
| Détection close | **Polling** en V1 ; WebSocket en phase 2 |
| Symboles | **Whitelist stricte** — un symbole inconnu n'est JAMAIS tradé |
| Feedback | MAJ DB + **nouveau topic `execution.events`** + décrément exposition + resync boot |

## 4. Architecture

Nouveau microservice suivant le pattern des services existants (`create_app`,
`EventConsumer`/`EventProducer`, `Cache` Redis, DB partagée via `cmi_common`).

```
services/trading-engine/
  app/
    main.py         # entrypoint : startup/shutdown, wiring consumer + poller de réconciliation
    engine.py       # TradingEngine.handle(RiskApprovedEvent) — orchestration métier
    kraken.py       # KrakenFuturesClient (httpx async, signature Authent, 3 modes)
    sizing.py       # calcul de taille de position (notional -> contrats)
    symbols.py      # mapping strict "SOL" -> "PF_SOLUSD" + whitelist
    guards.py       # garde-fous : kill-switch, plafond notional, levier, rate-limit
    reconcile.py    # poller positions/ordres Kraken (détection close + resync au boot)
    config.py       # TradingConfig depuis l'environnement
    __init__.py
  pyproject.toml    # dépend de cmi-common + httpx
```

### Unités et responsabilités

- **`engine.py`** — reçoit un `RiskApprovedEvent`, applique garde-fous → mapping → sizing →
  entrée → SL/TP → feedback. Ne connaît pas les détails HTTP (délègue à `kraken.py`).
- **`kraken.py`** — seul point qui parle à Kraken. Encapsule la signature, les 3 modes, et
  expose des méthodes typées. En `dry_run`, ne fait aucun appel réseau et retourne des
  réponses simulées déterministes.
- **`sizing.py`** — pur/déterministe, testable sans I/O.
- **`symbols.py`** — mapping + validation whitelist, pur/déterministe.
- **`guards.py`** — lit l'état Redis (kill-switch, compteur d'ordres) et applique les plafonds.
- **`reconcile.py`** — boucle de fond ; source de vérité = Kraken ; ne gère QUE les positions
  ouvertes par l'engine (suivies par `cliOrdId`).

## 5. Cycle de vie d'un ordre (`engine.handle`)

Entrée : un `RiskApprovedEvent`. Étapes :

1. **Garde-fous** (`guards.py`) :
   - Kill-switch : `TRADING_ENABLED=false` ou clé Redis `trading:enabled` == `false` → skip
     + `ExecutionEvent(kind=rejected, reason="kill_switch")`.
   - Rate-limit : plus de `MAX_ORDERS_PER_HOUR` sur la fenêtre glissante (compteur Redis) →
     skip + `rejected(reason="rate_limit")`.
2. **Mapping symbole** (`symbols.py`) : `SOL` → `PF_SOLUSD`. **Symbole hors whitelist → JAMAIS
   tradé** : `rejected(reason="unknown_symbol")` (jamais de skip silencieux).
3. **Idempotence** : `cliOrdId = event_id`. Si `trading:submitted:{event_id}` existe déjà dans
   Redis → ignore (Kafka est at-least-once, le handler doit être idempotent).
4. **Sizing** (`sizing.py`) :
   - `equity` = solde du compte Kraken (`get_accounts()`).
   - `notional = equity × position_size_pct`, plafonné par `MAX_ORDER_USD`.
   - Levier effectif plafonné par `MAX_LEVERAGE`.
   - `taille_contrats = notional / entry_price`, arrondi au pas de contrat Kraken. Taille en
     dessous du minimum Kraken → `rejected(reason="below_min_size")`.
5. **Entrée** : ordre `lmt` @ `entry_price`, côté `buy` (LONG) ou `sell` (SHORT), `cliOrdId`.
   - Marque `trading:submitted:{event_id}` en Redis, statut trade `submitted`,
     `ExecutionEvent(kind=submitted)`.
   - Si non rempli après `ENTRY_TIMEOUT_S` : `cancel_order()` puis renvoi en `mkt` (fallback).
   - Rejet Kraken → statut `failed`, `ExecutionEvent(kind=failed, reason=...)`.
6. **SL/TP** (après fill de l'entrée) : deux ordres **reduce-only** :
   - `stp` (stop) @ `stop_loss`.
   - `take_profit` @ `take_profit`.
   - Sens opposé à l'entrée. Enregistrement des `order_id` Kraken.
7. **Feedback fill** : statut trade `filled` (avec `fill_price` réel + `kraken_order_id`),
   `ExecutionEvent(kind=filled)`. Ajout de la position au set suivi Redis
   `trading:positions` (clé = `event_id`, valeur = symbole + order ids + taille + sens).

Toute exception est loggée ; l'offset Kafka n'est committé qu'après succès du handler
(retry at-least-once). Les rejets métier (garde-fous, symbole inconnu) sont des issues
**terminales** (offset committé) — on ne veut pas rejouer indéfiniment un signal invalide.

## 6. Détection de fermeture & réconciliation (`reconcile.py`)

Boucle de fond toutes les `RECONCILE_INTERVAL_S` (défaut 10s) :

1. Récupère les positions ouvertes réelles Kraken (`get_open_positions()`).
2. Pour chaque position **suivie** par l'engine (`trading:positions`) absente côté Kraken →
   la position a été fermée (SL ou TP touché, ou close manuel) :
   - Statut trade `closed`, calcul du PnL réel (fill de sortie vs entrée).
   - `ExecutionEvent(kind=closed, pnl=...)`.
   - **Décrément de `risk:exposure`** dans Redis de `position_size_pct` (corrige le trou
     actuel : l'exposition n'était jamais libérée).
   - Retrait de `trading:positions`.
3. **Position Kraken inconnue de l'engine** (non présente dans `trading:positions`) : **alerte
   loggée (warning) mais AUCUNE action** — jamais de fermeture aveugle d'une position qu'on
   n'a pas ouverte.

**Au démarrage** : le même passage resynchronise l'état (positions rouvertes après crash),
sans dupliquer d'ordres puisque l'idempotence repose sur `cliOrdId`.

*Extension phase 2* : une source WebSocket `fills` remplacera/complétera le polling en
implémentant la même interface de callback de fermeture.

## 7. Client Kraken Futures (`kraken.py`)

`httpx.AsyncClient` signant chaque requête privée : header `Authent` = HMAC-SHA512
(base64) sur `postData + nonce + endpointPath`, clé secret décodée en base64. Base URL selon
le mode :

- `dry_run` → aucun appel réseau ; réponses simulées déterministes + log détaillé.
- `demo` → `https://demo-futures.kraken.com`.
- `live` → `https://futures.kraken.com`.

Méthodes exposées : `get_accounts()`, `send_order(...)`, `cancel_order(...)`,
`get_open_positions()`, `get_open_orders()`, `get_fills(...)`.

Secrets lus depuis `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` (jamais loggés).

## 8. Configuration (env)

```
TRADING_MODE=dry_run            # dry_run | demo | live  (défaut dry_run)
KRAKEN_API_KEY=...
KRAKEN_API_SECRET=...
TRADING_ENABLED=true            # kill-switch statique ; complété par la clé Redis trading:enabled
MAX_ORDER_USD=500               # plafond notional par ordre
MAX_LEVERAGE=3                  # levier max forcé côté engine
MAX_ORDERS_PER_HOUR=10          # rate-limit métier
ENTRY_TIMEOUT_S=30              # délai avant fallback market
RECONCILE_INTERVAL_S=10         # période du poller de réconciliation
```

## 9. Ajouts partagés (`cmi_common`)

- **`events/execution.py`** : nouvel `ExecutionEvent(BaseEvent)` — `kind`
  (`submitted`/`filled`/`closed`/`failed`/`rejected`), `symbol`, `direction`,
  `kraken_order_id`, `fill_price`, `size`, `pnl`, `reason`, `decision_event_id`.
  `partition_key()` = `symbol`.
- **`kafka/topics.py`** : nouveau `Topic.EXECUTION = "execution.events"` + entrées dans
  `TOPIC_EVENT` et `TOPIC_PARTITIONS`.
- **`db/models.py`** : `Trade` — nouvelles colonnes `kraken_order_id: str | None`,
  `fill_price: float | None`, `pnl: float | None` ; élargissement de l'enum `status`
  (`approved` → `submitted` → `filled` → `closed` / `failed` / `rejected`).
- **api-gateway persister** & **websocket-gateway** : ajout de la consommation
  d'`execution.events` (mise à jour de la ligne `trades` + diffusion temps réel).

## 10. Flux de données (vue d'ensemble)

```
risk.approved.events (Kafka)
        │
        ▼
   TradingEngine.handle
        │  guards → symbols → sizing
        ▼
   KrakenFuturesClient  ──►  Kraken Futures (limit + fallback market, SL/TP reduce-only)
        │
        ├─►  DB trades (submitted/filled/closed/failed/rejected)
        ├─►  execution.events (Kafka)  ──►  api-gateway + websocket-gateway
        └─►  Redis (trading:positions, trading:submitted:*, décrément risk:exposure)
                 ▲
                 │
        reconcile.py (poller 10s)  ──►  détection close + resync boot
```

## 11. Tests

- **Unitaires** :
  - `sizing.py` : arrondis, plafond `MAX_ORDER_USD`, plafond levier, taille sous le minimum.
  - `guards.py` : kill-switch (env + Redis), rate-limit fenêtre glissante.
  - `symbols.py` : mapping, rejet strict d'un symbole hors whitelist.
  - `kraken.py` : correction de la signature `Authent` (vecteur de test connu), routage des
    modes (dry_run = zéro appel réseau).
  - `engine.handle` : cycle complet avec `KrakenFuturesClient` mocké — LONG et SHORT,
    fallback market, rejet, idempotence sur redelivery.
  - `reconcile` : détection de fermeture + décrément exposition ; position inconnue non touchée.
- **Intégration** : le mode `dry_run` fournit un test bout-en-bout sans toucher Kraken ;
  validation manuelle en `demo` avant tout passage `live`.

## 12. Déploiement

Ajout du service `trading-engine` au `docker-compose.yml` sur le modèle de `risk-engine`
(mêmes dépendances Kafka / Redis / Postgres, variables d'env de la section 8). Démarrage en
`dry_run` par défaut.

## 13. Risques & mitigations

- **Perte d'argent réelle** → défaut `dry_run`, mode `demo` obligatoire avant `live`, kill-switch,
  plafonds notional/levier/rate.
- **Double exécution** (Kafka at-least-once) → idempotence par `cliOrdId = event_id`.
- **Divergence d'état après crash** → réconciliation au démarrage + poller.
- **Position orpheline** (SL/TP posé mais service down au fill) → SL/TP vivent côté Kraken ;
  le poller rattrape la fermeture au redémarrage.
- **Symbole/paire inexistant côté Kraken** → whitelist stricte, rejet tracé.
