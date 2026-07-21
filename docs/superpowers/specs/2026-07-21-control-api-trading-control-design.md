# Design — `control-api` (control-plane front) + pilotage du trading-engine

- **Date** : 2026-07-21
- **Statut** : conception validée (brainstorming) — prêt pour plan d'implémentation
- **Auteur** : Claude Code + utilisateur
- **Dépend de** : `docs/superpowers/specs/2026-07-21-kraken-trading-engine-design.md` (trading-engine)

## 1. Contexte & problème

Le `trading-engine` exécute les signaux sur Kraken Futures mais n'est pilotable que par
variables d'environnement au démarrage. Le frontend Next.js possède déjà un contrat de
contrôle complet (`tradingApi`, `riskApi`, settings) mais **entièrement mocké** ; l'`api-gateway`
réel est en lecture seule (3 endpoints intelligence). Il n'existe aucune API réelle pour
piloter le bot depuis le front.

Objectif : donner à l'opérateur le **contrôle complet** du bot depuis le frontend (modes,
kill-switch, plafonds, auto-trading, actions sur positions, approbation d'opportunités, ordre
manuel), via un **service dédié découplé** du bot de trading, en s'appuyant sur les outils du
projet (Kafka, Redis, Postgres, FastAPI).

## 2. Décisions clés (validées)

| Sujet | Décision |
|-------|----------|
| Rôle du service | **control-plane dédié** `control-api` ; api-gateway garde les lectures intelligence |
| Transport commandes | **Kafka** (`control.commands`) **+ Redis runtime** ; le moteur applique, le hot-path lit Redis |
| Réglages exposés | mode, kill-switch, plafonds de sécurité, auto-trading |
| Actions manuelles | fermer position, ajuster SL/TP, ordre manuel, approuver/rejeter opportunités |
| Auto-trading OFF | **file d'attente** : le signal devient une opportunité en attente, exécutée sur approbation |
| Garde-fous | **unifiés** : actions manuelles soumises aux mêmes garde-fous que le flux auto |
| Auth | **JWT HS256 mutualisé** dans `cmi_common` (réutilise le mécanisme websocket-gateway) |
| Modes front | **3 modes explicites** : `dry_run` / `demo` / `live` |
| Lecture historique | control-api lit la **DB en lecture seule** (comme api-gateway) |
| Spec | **un seul spec global**, implémentation découpée en 4 phases A→D |

## 3. Architecture & flux

```
Frontend ──REST (JWT)──► control-api ──Kafka (control.commands)──► trading-engine ──► Kraken
                            │  ▲                                          │
                            │  └── lecture état (Redis: runtime/positions/pending)
                            └───── lecture historique (DB read-only)
   Frontend ◄──WebSocket── websocket-gateway ◄──── execution.events (temps réel)
```

**Invariant clé** : le `trading-engine` est le **seul writer** de l'état runtime et le seul à
parler à Kraken. control-api **publie des commandes** (mutations) et **lit l'état** (jamais
d'écriture Kraken, jamais d'écriture directe de `trading:runtime`).

## 4. État runtime dans Redis

Le moteur lit aujourd'hui sa config de l'env au démarrage. On introduit une config runtime
Redis `trading:runtime` (JSON) relue à chaque signal / action :

```json
{
  "mode": "dry_run",
  "trading_enabled": true,
  "auto_trading_enabled": true,
  "max_order_usd": 500.0,
  "max_leverage": 3.0,
  "max_orders_per_hour": 10,
  "entry_timeout_s": 30,
  "reconcile_interval_s": 10
}
```

- Les variables d'environnement (`TradingConfig.from_env`) deviennent les **défauts** : au boot,
  le moteur écrit `trading:runtime` **s'il est absent** (ne réécrase jamais une valeur déjà
  posée par l'opérateur).
- Le hot-path (`engine.handle`, guards, sizing) lit une **vue effective** = défauts + overlay
  Redis. Un helper `RuntimeConfig.load(cache, defaults)` renvoie un `TradingConfig` effectif.
- Rétro-compat : la clé existante `trading:enabled` (kill-switch du trading-engine) est
  fusionnée dans `trading_enabled` de `trading:runtime` (une seule source de vérité ; migration
  documentée dans le plan).

## 5. Canal de commandes (Kafka)

Nouveau topic **`control.commands`** + event **`ControlCommandEvent`** (`cmi_common/events/control.py`) :

```
event_type = "ControlCommandEvent"
source     = Source.CONTROL_API
command    : ControlCommand   # enum
payload    : dict[str, Any]    # paramètres spécifiques à la commande
issued_by  : str | None        # sub du JWT (audit)
```

`ControlCommand` (enum) et effet côté trading-engine :

| command | payload | effet |
|---------|---------|-------|
| `set_mode` | `{mode}` | met à jour `trading:runtime.mode` |
| `set_kill_switch` | `{enabled}` | met à jour `trading:runtime.trading_enabled` |
| `set_auto_trading` | `{enabled}` | met à jour `trading:runtime.auto_trading_enabled` |
| `set_caps` | `{max_order_usd?, max_leverage?, max_orders_per_hour?, entry_timeout_s?, reconcile_interval_s?}` | met à jour les plafonds (champs fournis uniquement) |
| `close_position` | `{event_id}` | close market reduce-only de la position suivie |
| `adjust_sltp` | `{event_id, stop_loss?, take_profit?}` | cancel + replace des ordres SL/TP reduce-only |
| `manual_order` | `{symbol, side, order_type, quantity, price?}` | ordre manuel (mêmes garde-fous) |
| `approve_opportunity` | `{event_id}` | exécute une opportunité en attente |
| `reject_opportunity` | `{event_id, reason?}` | annule une opportunité en attente |

Le trading-engine consomme `control.commands` via un handler dédié (`ControlHandler.handle`) qui
dispatche par `command`. Chaque application émet un `ExecutionEvent` de retour (temps réel +
audit). Les commandes sont idempotentes par nature (settings) ou protégées par l'état des
positions/pending (actions).

## 6. Gate human-in-the-loop (auto-trading)

Dans `engine.handle(RiskApprovedEvent)`, après idempotence + whitelist :
- Si `auto_trading_enabled` = **ON** → exécution immédiate (comportement trading-engine actuel).
- Si **OFF** → le signal est **mis en file** : payload stocké dans `trading:pending:{event_id}`
  (+ set `trading:pending`), `ExecutionEvent(kind=pending)` émis, **aucun ordre Kraken**.

Sur commande `approve_opportunity {event_id}` : le moteur relit le payload en attente, le retire
de la file, puis exécute exactement comme un signal auto (guards → sizing → entrée → SL/TP).
Sur `reject_opportunity` : `ExecutionEvent(kind=rejected, reason="operator_reject")` + retrait.

`ExecutionKind` gagne une valeur **`PENDING`**.

## 7. Garde-fous unifiés

Les actions `manual_order`, `close_position`, `adjust_sltp` et l'exécution d'une opportunité
approuvée passent par les **mêmes** garde-fous que le flux auto (`check_guards` : kill-switch +
rate-limit ; whitelist ; plafonds notional/levier via `sizing`). Un ordre manuel hors whitelist
est rejeté (`ExecutionEvent(kind=rejected, reason="unknown_symbol")`), exactement comme un signal.

## 8. Auth (JWT mutualisé)

Extraction de la logique JWT HS256 (aujourd'hui dans `services/websocket-gateway/app/auth.py`,
stdlib pure) vers **`cmi_common/auth.py`** :
- `decode_token(token) -> Principal` (comportement identique : vérifie si `JWT_SECRET` posé,
  sinon décode non vérifié en dev).
- Ajout `encode_token(claims, secret, ttl_seconds) -> str` (HS256) pour l'émission.
- `Principal`, `InvalidTokenError` déplacés dans le module partagé.

control-api :
- **`POST /auth/login`** : valide des identifiants admin (`CONTROL_ADMIN_USER` /
  `CONTROL_ADMIN_PASSWORD` en env) et renvoie un JWT HS256 signé avec `JWT_SECRET`.
- Dépendance FastAPI **`require_principal`** (`app/auth_dep.py`) protégeant tous les endpoints de
  contrôle (Bearer token). En l'absence de `JWT_SECRET` (dev), comportement permissif identique
  au websocket-gateway.

Le websocket-gateway migre vers `cmi_common.auth` (suppression de son `auth.py` local, import du
module partagé) — comportement inchangé, couvert par ses tests existants.

## 9. Service `control-api`

```
services/control-api/
  app/
    main.py            # FastAPI (create_app), producer Kafka, Cache Redis, Database (read-only),
                       #   montage routers + /auth/login
    auth_dep.py        # require_principal (dépendance JWT)
    commands.py        # CommandPublisher.publish(ControlCommandEvent) -> control.commands
    state.py           # StateReader : lecture Redis (trading:runtime, trading:positions/*,
                       #   trading:pending/*) + DB read-only (trades)
    routers/
      settings.py      # GET /trading/status, GET /trading/settings ;
                       #   POST /trading/mode|/trading/auto|/trading/kill|/trading/caps
      positions.py     # GET /trading/positions ; POST /trading/positions/{id}/close ;
                       #   PATCH /trading/positions/{id}/sltp
      opportunities.py # GET /trading/opportunities ; POST /trading/opportunities/{id}/approve|reject
      orders.py        # POST /trading/orders (ordre manuel)
      auth.py          # POST /auth/login
  pyproject.toml       # depends cmi-common
```

Unités & responsabilités :
- **`commands.py`** — seul point qui publie sur `control.commands` (sérialise `ControlCommandEvent`).
- **`state.py`** — seul point de lecture d'état (Redis live + DB historique) ; aucune écriture.
- **routers** — validation Pydantic des entrées + traduction en commande ou en lecture ; fins.
- **`auth_dep.py`** — garde JWT ; réutilise `cmi_common.auth`.

## 10. Trading-engine — changements

- **`app/runtime.py`** (nouveau) : `RuntimeConfig` — `load(cache, defaults)` (défauts env +
  overlay Redis) et helpers d'écriture des settings (`set_field`) appelés par le ControlHandler.
- **`app/control.py`** (nouveau) : `ControlHandler.handle(ControlCommandEvent)` — dispatch des 9
  commandes (settings → Redis ; actions → Kraken via l'engine existant / reconcile).
- **`app/engine.py`** : lit la config effective via `RuntimeConfig.load` ; ajoute le gate
  auto-trading (file d'attente) ; expose des méthodes réutilisables pour les actions manuelles
  (entrée, close reduce-only, replace SL/TP) partageant les garde-fous.
- **`app/main.py`** : ajoute un second consumer `[Topic.CONTROL]` → `ControlHandler.handle` ;
  écrit les défauts runtime au boot.

## 11. Frontend (Next.js)

- **Types** : `TradingMode` → `'dry_run' | 'demo' | 'live'` ; `TradingStatus` étendu
  (`trading_enabled`, `auto_trading_enabled`, `caps`) ; type `EngineSettings`.
- **Transport réel** (`NEXT_PUBLIC_USE_MOCK=0`) : `tradingApi`/`riskApi`/settings pointent vers
  control-api via le proxy `/api/gateway/*` existant. Les routes `src/app/api/mock/*` restent
  pour `USE_MOCK=1`.
- **`endpoints.ts`** : ajoute `settingsApi` (get settings, setKill, setCaps) ; met à jour
  `tradingApi.setMode` (3 modes) ; conserve close/adjust/approve/reject/placeOrder.
- **UI** (composants existants à câbler) : `EngineControlCard` (mode + kill + auto),
  panneau plafonds dans `settings/`, `PositionsTable` (close + SL/TP), `OpportunitiesSection`
  (approve/reject), `ManualOrderCard`. **Confirmation forte** (double validation) pour le
  passage en `live`.

## 12. Ajouts partagés (`cmi_common`)

- `events/control.py` : `ControlCommandEvent` + enum `ControlCommand` ; `Source.CONTROL_API`,
  `EventType.CONTROL_COMMAND`.
- `kafka/topics.py` : `Topic.CONTROL = "control.commands"` (+ TOPIC_EVENT, TOPIC_PARTITIONS=3).
- `events/execution.py` : `ExecutionKind.PENDING`.
- `auth.py` : JWT partagé (`decode_token`, `encode_token`, `Principal`, `InvalidTokenError`).

## 13. Découpage en phases (chaque phase livrable indépendamment)

- **Phase A — Réglages** : `cmi_common` (control event/topic, JWT partagé) ; runtime config Redis ;
  ControlHandler (settings uniquement) ; control-api (auth + settings router) ; front réglages
  (mode/kill/auto/caps). Cœur du contrôle.
- **Phase B — Positions** : `close_position` + `adjust_sltp` (ControlHandler + engine) ; router
  positions ; front positions.
- **Phase C — Human-in-the-loop** : gate auto-trading + file `trading:pending` + `ExecutionKind.PENDING`
  + `approve/reject` ; router opportunities ; front opportunités.
- **Phase D — Ordre manuel** : `manual_order` (garde-fous unifiés) ; router orders ; front ordre manuel.

## 14. Tests

- **cmi_common** : sérialisation `ControlCommandEvent` (round-trip parse_event), `Topic.CONTROL`,
  `ExecutionKind.PENDING`, JWT `encode_token`/`decode_token` (round-trip + rejet mauvaise signature
  quand `JWT_SECRET` posé).
- **trading-engine** : `RuntimeConfig.load` (défauts vs overlay Redis) ; ControlHandler dispatch
  (chaque commande → effet Redis/Kraken mocké) ; gate auto-trading (ON exécute / OFF met en file) ;
  approve rejoue et exécute, reject retire ; garde-fous appliqués aux actions manuelles.
- **control-api** : chaque router avec `CommandPublisher` mocké + `StateReader` mocké + auth
  (endpoint protégé rejette sans token quand `JWT_SECRET` posé) ; `/auth/login` renvoie un JWT
  décodable.
- **Intégration** : mode `dry_run` = bout-en-bout sans Kraken (settings → commande → runtime →
  gate → pending → approve → exécution simulée).
- **Frontend** : tests existants + câblage vérifié en `USE_MOCK=0` contre un control-api local
  (manuel dans la validation de phase).

## 15. Déploiement

Service `control-api` ajouté au `docker-compose.yml` sur le modèle des services FastAPI
(shared `docker/Dockerfile` + `SERVICE_PATH` + anchors `*service-defaults`/`*common-env`),
`depends_on` kafka + redis + postgres. Variables : `JWT_SECRET`, `CONTROL_ADMIN_USER`,
`CONTROL_ADMIN_PASSWORD`, config DB/Kafka/Redis héritée. Le frontend `USE_MOCK` bascule sur `0`
et pointe le proxy vers control-api.

## 16. Risques & mitigations

- **Contrôle live sensible** → JWT obligatoire (`JWT_SECRET`), confirmation forte pour `live`,
  garde-fous unifiés, défaut `dry_run`.
- **Course entre commandes et hot-path** → moteur seul writer de `trading:runtime` ; lectures
  atomiques JSON ; commandes settings idempotentes.
- **Opportunité approuvée obsolète** (prix bougé) → l'exécution repasse par guards + sizing au
  moment de l'approbation ; TTL sur `trading:pending:{event_id}` (documenté dans le plan).
- **Divergence kill-switch legacy** (`trading:enabled` vs `trading:runtime`) → fusion en une
  source unique, migration au boot documentée.
- **Double exécution manuel/auto** sur un même symbole → suivi par `event_id`/position ; l'ordre
  manuel est distinct (cliOrdId propre) et soumis au rate-limit.
