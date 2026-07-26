# Command Center opérationnel — pipeline, Kraken, persistance du flux

**Date:** 2026-07-25
**Statut:** design validé, prêt pour plan d'implémentation

## Problème

Trois symptômes observés sur le Command Center en production, de causes distinctes :

1. Le flux d'événements temps réel ne survit pas à un changement de page — aucune
   persistance, aucune pagination.
2. Le pipeline s'arrête à Haiku : aucune décision Sonnet, et les pages Capital,
   Risque et Trading restent vides.
3. Le solde Kraken affiché ne correspond à aucun compte réel.

Le mode `dry_run` n'est la cause d'aucun des trois.

## Diagnostic

### Flux non persisté

`frontend/src/lib/ws/WebSocketProvider.tsx:29-44` — le feed est un `useState` en
mémoire plafonné à `MAX_FEED = 200`. Rien n'est écrit. En mode mock, le backfill
`/api/mock/stream/recent?since=0` donne l'illusion de persistance ; en live ce
backfill n'existe pas.

`services/api-gateway/app/persister.py` ne persiste que `Price`, `Signal`,
`Decision`, `Trade`, plus une mise à jour sur `ExecutionEvent`. Sentiment, Volume
et Dex ne sont persistés nulle part. Il n'existe ni table de flux unifié ni
endpoint paginé.

### Pipeline bloqué — trois verrous en série

| Verrou | Condition | Emplacement |
|---|---|---|
| Haiku → Sonnet | `score >= 60` **et** (`ambiguous` ou `vol >= 0.6` ou `mom >= 0.6`) | `services/ai-worker-haiku/app/scorer.py:105` |
| decision-engine | `score >= 70` | `services/decision-engine/app/engine.py:27` |
| risk-engine | `score >= 70` **et** `confidence >= 0.55` | `services/risk-engine/app/rules.py:15-16` |

Le score combine `0.35·mom + 0.25·vol + 0.25·sent + 0.15·liq`. Deux des quatre
facteurs sont quasi toujours absents :

- `volume_spike_ratio` — `collector-coingecko` n'émet un `VolumeEvent` que sur
  turnover anormal (`app/domain/mapper.py:38`), donc `vol = 0` la plupart du temps.
- `liquidity_usd` — n'arrive que via DexScreener, qui ne couvre pas les majeures ;
  la valeur inconnue retombe sur un neutre `liq_f = 0.5`.

Cas réaliste (variation 24 h +3 %, sentiment +0.3, pas de spike volume, liquidité
inconnue) : score ≈ 22. Atteindre 60 exigerait un mouvement proche de 15 % **et**
un sentiment saturé. `escalate` est donc presque toujours faux, et Sonnet sort
immédiatement (`services/ai-worker-sonnet/app/worker.py:55`).

**Contradiction supplémentaire** dans `scorer.py:88` :

```python
confidence = 0.3 + 0.4 * liq_f + (0.0 if ambiguous else 0.3)
```

Avec `liq_f = 0.5` (liquidité inconnue), un signal *ambigu* obtient `0.50`, sous
le plancher `min_confidence = 0.55` du risk-engine. Le scorer escalade
précisément les setups ambigus vers Sonnet, alors que ces mêmes setups sont
construits pour être refusés par le risque. Baisser les seuils de score ne
lèverait pas ce verrou.

`min_risk_reward = 1.5` passe toujours : `_compute_levels` produit un RR fixe de
2.0 (SL 5 % / TP 10 %). Ce n'est pas un blocage.

**Cascade** : 0 `DecisionEvent` → 0 `RiskApprovedEvent` → 0 ligne `trades`. Comme
Capital, Risque et Trading dérivent tous de la table `trades`
(`services/api-gateway/app/read_api.py:514-614`), les trois pages sont vides pour
une cause unique.

### Solde Kraken fictif

`services/api-gateway/app/read_api.py:581` :

```python
"kraken_balance_usd": round(cash * 0.8, 2),
```

80 % d'un cash lui-même dérivé de `BASE_CAPITAL = os.getenv("CMI_BASE_CAPITAL_USD",
"100000")`, un capital synthétique. Aucun code du plan de lecture n'appelle
Kraken. Le seul client (`services/trading-engine/app/kraken.py`) expose
`get_accounts()` mais il n'est appelé nulle part, et en `dry_run` il renvoie
`{"flex": {"portfolioValue": 10000.0}}` en dur (`kraken.py:127`).

Ce client vise **Kraken Futures** (`futures.kraken.com/derivatives`). Un
portefeuille spot relève d'une autre API (`api.kraken.com`) avec un autre schéma
de signature et d'autres clés.

Les clés Kraken ne sont câblées que dans `trading-engine`
(`docker-compose.vps.yml:285-286`).

### Constat annexe

`PortfolioChangedEvent` et `PositionChangedEvent` sont affichés par
`frontend/src/components/command/LiveEventStream.tsx:8` et
`frontend/src/components/realtime/LiveFeed.tsx:32-33`, mais n'existent pas côté
Python : ni classe d'événement, ni topic dans
`libs/cmi_common/cmi_common/kafka/topics.py`, ni producteur. Vestiges du mock.

## Principe directeur

**Ne jamais inventer une valeur.** C'est la nature exacte du bug initial :
`cash * 0.8` était plausible et faux, donc plus nuisible qu'une absence de
valeur. Une donnée indisponible est `null` avec un statut explicite ; une donnée
périmée est servie marquée `stale`.

---

## Phase 1 — Débloquer le pipeline

Instrumenter d'abord, calibrer ensuite sur les chiffres observés.

### 1a. Instrumentation (aucun changement de comportement)

Les facteurs normalisés sont calculés puis jetés. On les conserve :

- Sur `signals` : colonnes `ambiguous`, `block_reason`
  (`score_below_threshold` | `gate_not_met` | `escalated`), `factors_present`
  (0-4). Les quatre facteurs normalisés vont dans le `payload` JSONB déjà écrit
  par `persister.py:78`.

  `factors_present` est le nombre de facteurs réellement renseignés parmi
  `price_change_pct_24h`, `volume_spike_ratio`, `sentiment_score`,
  `liquidity_usd`. Il se dérive directement du dict de features, sans attendre
  1c ; le scorer le calcule et l'expose sur l'`AnalysisEvent` dès 1a, ce qui
  permet de mesurer la couverture des facteurs *avant* de décider s'il faut les
  enrichir.
- Nouvelle table `pipeline_rejections` (`time`, `stage`, `symbol`,
  `correlation_id`, `reason`, `score`, `confidence`).

  Le risk-engine émet **déjà** un `RiskRejectedEvent` sur le topic
  `decision.events` comme piste d'audit (`engine.py:100-112`,
  `libs/cmi_common/cmi_common/events/risk.py:64`), et l'api-gateway consomme déjà
  ce topic (`main.py:23`). Persister ces refus ne demande donc qu'une branche
  supplémentaire dans `Persister.handle` — aucun nouveau canal.

  Le decision-engine, lui, abandonne silencieusement (`engine.py:53-54`,
  `return` nu) : c'est le seul étage à instrumenter réellement. Il émettra un
  `RiskRejectedEvent` avec `source = DECISION_ENGINE` et un `reason` structuré,
  réutilisant l'événement existant plutôt que d'en créer un second de même forme.

Nouvel endpoint `GET /systems/funnel?window=24h` :

```
{
  "window": "24h",
  "stages": [
    {"stage": "events_in",  "count": N},
    {"stage": "analyses",   "count": N},
    {"stage": "escalated",  "count": N},
    {"stage": "decisions",  "count": N},
    {"stage": "approved",   "count": N},
    {"stage": "executed",   "count": N}
  ],
  "score_deciles": [...],
  "top_block_reasons": [{"stage": "...", "reason": "...", "count": N}],
  "factors_presence": {"0": N, "1": N, "2": N, "3": N, "4": N},
  "updated_at": "..."
}
```

Un panneau « Entonnoir » dans le Command Center le rend visible.

`factors_present` est exposé parce qu'un score calculé sur 2 facteurs sur 4 n'est
pas comparable à un score sur 4/4 ; sans cette dimension, la calibration se
ferait sur une moyenne trompeuse.

### 1b. Seuils configurables + correction de la confiance

**Trois des quatre seuils sont déjà configurables** — vérification faite dans le
code :

| Seuil | Variable | Statut |
|---|---|---|
| decision-engine | `DECISION_THRESHOLD` | existe (`decision-engine/app/main.py:15`) |
| risk-engine score | `RISK_MIN_SCORE` | existe (`risk-engine/app/main.py:24`) |
| risk-engine confiance | `RISK_MIN_CONFIDENCE` | existe (`risk-engine/app/main.py:23`) |
| escalade Haiku | `HAIKU_ESCALATE_SCORE` | **à créer** |

Seul le scorer Haiku n'a aucun câblage : `main.py:22` construit
`HaikuWorker(FeatureStore(cache), producer)` sans `scorer_config`, donc les
défauts de `ScorerConfig` sont figés dans le code. C'est le seul travail de
configuration réel de cette phase. Les variables gardent les valeurs actuelles
comme défaut ; ce changement ne modifie donc rien au comportement.

On conserve la convention de nommage existante (`RISK_*`, `DECISION_*`) plutôt
que d'introduire un préfixe `CMI_` concurrent.

Les valeurs cibles seront fixées après observation de l'entonnoir sur une période
réelle. Aucune valeur n'est proposée ici : ce serait le calibrage à l'aveugle que
l'instrumentation vise justement à éviter.

Une correction ne dépend pas des données et fait partie de 1b : **découpler la
confiance de l'ambiguïté**. L'ambiguïté est une raison d'escalader vers un
analyste, pas de réduire la confiance sous le plancher du risque. Elle devient un
champ propre de l'`AnalysisEvent`, et la confiance ne dépend plus que de la
qualité des données (liquidité, fraîcheur, nombre de facteurs présents).

### 1c. Enrichir les features

- `collector-coingecko` émet le `volume_spike_ratio` **systématiquement** au lieu
  de ne le faire que sur turnover anormal. Décider si un ratio de 1.0 est
  intéressant relève du scorer, pas du collecteur.
- Pour les paires listées en CEX (non couvertes par DexScreener), dériver un
  proxy de liquidité depuis `volume_24h_usd`, déjà présent dans le `PriceEvent`,
  plutôt que de retomber sur le neutre 0.5. Le proxy vaut `volume_24h_usd` tel
  quel, injecté dans la même normalisation logarithmique que la liquidité DEX
  (`scorer.py:63-67`). Un champ `liquidity_source` (`dex` | `volume_proxy` |
  `unknown`) accompagne la valeur pour que l'entonnoir distingue une liquidité
  mesurée d'une liquidité estimée — sans quoi la calibration traiterait les deux
  comme équivalentes.

---

## Phase 2 — Vrai solde Kraken

### Principe : les clés restent dans trading-engine

L'api-gateway n'appelle pas Kraken. Il est le service exposé publiquement en
lecture ; lui confier des secrets d'exchange serait un mauvais échange. On
réutilise le chemin existant : trading-engine produit, api-gateway persiste, le
plan de lecture sert.

### Séparation lecture / écriture des identifiants

Nouvelle paire `KRAKEN_READ_API_KEY` / `KRAKEN_READ_API_SECRET`, créée côté
Kraken avec les seules permissions de consultation (« Query Funds »). Les clés
existantes conservent le droit de trader.

### Le mode ne gouverne que les écritures

Règle posée : `send_order` / `cancel_order` restent simulés en `dry_run` ; la
**consultation de solde** utilise le client lecture seule et interroge toujours
l'API réelle. Le vrai portefeuille s'affiche donc correctement en `dry_run`.

`get_open_positions()` reste en revanche piloté par le mode : le réconciliateur
(`services/trading-engine/app/reconcile.py`) compare les positions que *le bot* a
ouvertes, simulées en `dry_run`. Mélanger les deux usages ferait fermer à tort
des positions simulées. Ces deux usages sont donc portés par deux composants
distincts : le `AccountSnapshotProvider` (lecture seule, indépendant du mode) et
le `KrakenFuturesClient` d'exécution (piloté par le mode).

### Abstraction multi-venue

```
AccountSnapshotProvider  (protocole)
  ├── KrakenSpotProvider     api.kraken.com       /0/private/Balance, /0/private/TradeBalance
  └── KrakenFuturesProvider  futures.kraken.com   /api/v3/accounts
```

Les schémas de signature diffèrent : le spot signe
`HMAC-SHA512(secret_b64, path + SHA256(nonce + postdata))`, construction distincte
de celle des Futures implémentée dans `kraken.py:53-59`.

Chaque provider s'active indépendamment selon la présence de ses clés. Aucune clé
configurée → le venue est absent, pas en erreur.

**Résolu le 2026-07-26 : le compte est du Kraken *spot*.** L'opérateur a transmis
une paire de clés dont le format le détermine sans ambiguïté — clé publique de
56 caractères, secret base64 de 88 caractères terminé par `==`, qui est la forme
des clés `api.kraken.com`. Les clés Futures ont une forme différente.

Conséquence : `KrakenSpotProvider` est le composant à écrire, et le client Futures
existant (`trading-engine/app/kraken.py`) **n'aurait jamais pu montrer ce solde**,
quelle que soit la configuration. L'abstraction multi-venue reste néanmoins
justifiée : le compte Futures deviendra pertinent si le bot passe en `live`.

⚠️ **Les clés transmises sont à considérer comme compromises** — elles sont
passées en clair dans un transcript de conversation. En régénérer une paire
neuve, avec la seule permission « Query Funds », avant toute implémentation.

### Flux de donnée

Nouveau topic `account.snapshot.events` et nouvel événement `AccountSnapshotEvent`
(`venue`, `equity_usd`, `cash_usd`, `balances[]`, `fetched_at`) :

```
trading-engine (boucle périodique, CMI_ACCOUNT_POLL_S=60)
   └─ AccountSnapshotEvent
        ├─► Kafka ─► api-gateway ─► table account_snapshots (dernier état par venue)
        └─► Redis trading:account:{venue}   (lisible par control-api)
```

Le topic alimente aussi le websocket-gateway : le flux temps réel gagne un
événement « Portefeuille » réel, qui remplace les vignettes mortes
`PortfolioChangedEvent` / `PositionChangedEvent`.

### Fin de la fiction

`kraken_balance_usd = cash * 0.8` disparaît. Le champ devient la valeur réelle du
dernier snapshot, accompagné de :

- `balance_source` : `kraken_spot` | `kraken_futures` | `unavailable`
- `balance_fetched_at` : horodatage du snapshot
- `balance_stale` : vrai si le snapshot a plus de 5 minutes

Sans clé configurée ou en cas d'échec : `kraken_balance_usd: null` et
`balance_source: "unavailable"`. Le frontend affiche « — · non connecté » plutôt
qu'un nombre. Un snapshot périmé est grisé plutôt que présenté comme frais.

### Capital de référence

`BASE_CAPITAL` (100 000 $ par défaut) dimensionne aujourd'hui toutes les
positions, donc les pages Capital et Risque. Quand un snapshot réel est
disponible, c'est lui qui devient le capital de référence ;
`CMI_BASE_CAPITAL_USD` ne reste qu'en repli. Le plan de lecture expose lequel des
deux a servi. Sans cela, un vrai solde Kraken s'afficherait à côté de positions
dimensionnées sur un capital imaginaire.

---

## Phase 3 — Persistance et pagination du flux

### Deux hypertables, imposées par la rétention

La rétention TimescaleDB (`add_retention_policy`) supprime des **chunks entiers**
par tranche de temps et ne sait pas filtrer par type d'événement. Avec une table
unique, une rétention différenciée deviendrait un `DELETE ... WHERE event_type IN
(...)` planifié — un balayage coûteux sur une table chaude, à l'opposé d'un drop
de chunk quasi gratuit.

D'où deux hypertables au schéma identique, séparées par leur seul axe de
divergence, le volume :

| Table | Contenu | Rétention | Volume |
|---|---|---|---|
| `events_market` | Price, Volume, Dex | 7 j (`CMI_EVENTS_RETENTION_MARKET_D`) | élevé |
| `events_signal` | Sentiment, Analysis, Decision, RiskApproved, Execution, AccountSnapshot | 90 j (`CMI_EVENTS_RETENTION_SIGNAL_D`) | faible |

Schéma commun, aligné sur les conventions du projet (timestamps UTC naïfs via
`_naive_utc`, payload JSONB) :

```
time           TIMESTAMPTZ NOT NULL      -- occurred_at
event_id       TEXT NOT NULL
event_type     TEXT NOT NULL
topic          TEXT NOT NULL
symbol         TEXT NULL
correlation_id TEXT NULL
payload        JSONB NOT NULL
PRIMARY KEY (time, event_id)
```

> **Corrige le 2026-07-26.** La spec prevoyait `UNIQUE (event_id)` seul.
> TimescaleDB le refuse — verifie contre la base de production :
> `cannot create a unique index without the column "time" (used in partitioning)`.
> Tout index unique d'une hypertable doit contenir sa colonne de partitionnement.
>
> `PRIMARY KEY (time, event_id)` est equivalent pour l'idempotence Kafka : un
> message redelivre porte un evenement serialise identique, donc `occurred_at`
> et `event_id` le sont aussi. Meme forme que `decision_journal` et
> `pipeline_rejections`.
>
> Le type est `TIMESTAMPTZ`, pas `TIMESTAMP` : c'est ce que sont reellement
> toutes les hypertables du repo.

Index complementaires : `(time DESC, event_id DESC)` pour la pagination par
curseur, et `(correlation_id)` qui enrichit au passage `/trace/{cid}`.

### Écriture : un EventArchiver distinct du persister

Dans api-gateway, à côté de `Persister`, pas dedans. Les responsabilités
diffèrent : `Persister` projette des événements vers des tables **métier** (un
`RiskApprovedEvent` devient une ligne `Trade` avec un cycle de vie),
`EventArchiver` archive le flux brut sans interprétation. L'archiveur consomme
tous les topics de diffusion et route vers l'une des deux tables selon le type.

### Lecture : GET /events, curseur composite

```
GET /events?limit=100&types=DecisionEvent,RiskApprovedEvent&symbol=BTC
            &before=<iso_ts>_<event_id>

→ { "items": [...], "next_cursor": "<iso_ts>_<event_id>" | null }
```

Le curseur est composite parce que plusieurs événements partagent régulièrement
la même milliseconde ; un curseur sur le seul timestamp sauterait ou répéterait
des lignes. La requête interroge les deux tables et fusionne sur le même ordre
`(time DESC, event_id DESC)`.

L'endpoint entre dans le manifeste `services/api-gateway/app/read_contract.py` et
donc dans le test de parité `tests/test_read_contract.py`, conformément à la
convention du projet pour tout ajout au plan de lecture.

### Frontend : historique et live fusionnés

Le `WebSocketProvider` reste un transport pur. On ajoute un hook `useEventFeed`
combinant un `useInfiniteQuery` (react-query déjà présent) sur `/events` et
l'abonnement WS. La fusion se fait par `Map` clefée sur `event_id` : les frames
live s'insèrent en tête, les pages historiques s'ajoutent en bas, un événement
reçu par les deux chemins n'apparaît qu'une fois. La première page est chargée au
montage — revenir sur la page restitue le flux au lieu d'un écran vide.

### Nettoyage ciblé

Le Command Center a deux composants de flux redondants et déjà divergents (les
libellés diffèrent) : `LiveFeed.tsx` (138 l., filtres par catégorie) et
`LiveEventStream.tsx` (34 l., tampon de 40, clic → trace). Les brancher tous deux
sur le nouveau hook doublerait le travail ; on les fusionne en un composant
gardant les filtres de l'un et le clic-vers-trace de l'autre.

`PositionChangedEvent` et `PortfolioChangedEvent` sont retirés des libellés : rien
ne les produit, ils ne peuvent qu'induire en erreur. `AccountSnapshotEvent`
(phase 2) les remplace.

### Hors périmètre

Pas de rejeu depuis Kafka pour reconstruire l'historique. L'archive démarre au
déploiement ; le passé est perdu. C'est acceptable et évite un chantier de
backfill sans valeur.

---

## Migrations

Quatre, dans la numérotation existante (dernière : `0007_sentiment_agg_daily`) :

- `0008_signal_diagnostics` — colonnes `ambiguous`, `block_reason`,
  `factors_present` sur `signals` + table `pipeline_rejections`
- `0010_events_market` — hypertable + politique de rétention (phase 3)
- `0011_events_signal` — hypertable + politique de rétention (phase 3)
- `0012_account_snapshots` — dernier état de solde par venue (phase 2)

> **Renumérotés deux fois le 2026-07-26.** D'abord parce que `0009` a été pris
> par `0009_decision_journal` (journal contrefactuel), livré entre-temps. Puis
> parce que l'opérateur a choisi de livrer la phase 3 avant la phase 2 : les
> numéros suivent l'ordre de livraison réel, pas l'ordre de rédaction. Deux
> migrations portant le même numéro produisent deux têtes Alembic et bloquent le
> déploiement.

Chaque migration est additive : aucune colonne supprimée, aucun type modifié. Le
rollback se limite à un `DROP`, et un ancien conteneur encore en vol pendant le
déploiement continue de fonctionner — ce qui compte, le déploiement VPS étant un
`docker compose up` sur GHCR sans fenêtre de maintenance.

## Impacts du travail livré entre-temps (2026-07-26)

La phase 1 et le journal contrefactuel ont été livrés après la rédaction de cette
spec. Quatre points la modifient ; ils sont mécaniques mais chacun casserait
silencieusement.

### 1. Enregistrer tout nouvel événement dans l'union `AnyEvent`

`kafka/consumer.py` décode **chaque** message via `parse_event`, qui valide contre
l'union discriminée `AnyEvent` de `events/__init__.py`. Un événement absent de
cette union **se publie sans erreur et se fait rejeter à la consommation**.

Concerne `AccountSnapshotEvent` (phase 2) et tout événement de la phase 3.
Ajouter à l'union, pas seulement à `__all__`, et l'épingler par un test
d'aller-retour `parse_event(ev.as_kafka_value())`.

### 2. Le triplet de tables de topics est désormais gardé par un test

`tests/test_journal_topic.py::test_every_topic_appears_in_both_tables` échoue si
un membre de `Topic` manque dans `TOPIC_EVENT` ou `TOPIC_PARTITIONS`. C'est une
protection acquise : les nouveaux topics des phases 2 et 3 seront attrapés
automatiquement.

En revanche `scripts/create-topics.sh` reste hors de ce garde-fou, et il lui
manque déjà `execution.events` et `control.commands`. Y ajouter les nouveaux
topics explicitement — sinon ils sont créés avec les partitions par défaut du
broker au lieu de celles déclarées.

### 3. Phase 3 — l'archiveur doit-il archiver le journal ?

Le topic `journal.entries` existe désormais et l'archiveur de la phase 3 consomme
« tous les topics de diffusion ». Or le journal est **déjà** persisté dans
`decision_journal` avec 180 jours de rétention. L'archiver aussi doublerait le
stockage de la table la plus volumineuse du système sans rien apporter.

**Décision retenue : exclure `journal.entries` de l'archiveur.** Il n'est pas un
événement de marché diffusé au terminal, c'est un enregistrement d'audit qui a
déjà sa table et sa rétention propre.

### 4. Phase 3 — cohabitation dans le persister

`Persister.handle` dispatche désormais sept types par `isinstance`, dont
`JournalEntryEvent` placé avant `RiskRejectedEvent` et `DecisionEvent` (l'ordre
compte : plusieurs types transitent sur `decision.events`). L'`EventArchiver` de
la phase 3 reste un composant **distinct** du `Persister`, comme la spec le
prévoit déjà — cette séparation devient d'autant plus justifiée que le persister
s'est densifié.

---

## Gestion des erreurs

- **Kraken injoignable / clés absentes / erreur HTTP** → `kraken_balance_usd:
  null`, `balance_source: "unavailable"`. Jamais de repli sur une estimation. Le
  dernier snapshot connu est servi avec `stale: true` et son âge.
- **Archiveur en échec** (DB saturée, contention) → journalisé et compté en
  métrique, mais ne bloque jamais la diffusion WS. Perdre une ligne d'archive est
  acceptable ; interrompre le temps réel ne l'est pas. L'archivage est en aval de
  la diffusion.
- **Événement inconnu / non mappé** → archivé en `events_signal` avec son type
  brut plutôt que rejeté. Un type imprévu doit rester visible.
- **Rate limit Kraken** → backoff exponentiel sur la boucle de snapshot. À 60 s
  sur deux endpoints on est loin des limites, mais le backoff évite qu'un
  incident devienne une boucle serrée.

## Tests

pytest à plat dans `tests/`, comme le reste du projet. L'essentiel est testable
sans I/O, la logique en cause étant pure.

- `test_scorer_diagnostics` — table de vérité des trois verrous : pour un jeu de
  features donné, quel étage bloque et pourquoi. Ce test aurait attrapé la
  contradiction confiance/ambiguïté ; il la verrouille désormais.
- `test_kraken_spot_signing` — vecteur de signature connu. La signature spot ne se
  debug pas en production.
- `test_account_snapshot_fallback` — clés absentes, erreur HTTP, snapshot périmé :
  aucun chemin ne produit un nombre inventé.
- `test_events_cursor_pagination` — plusieurs événements au même timestamp ; un
  parcours complet par curseur ne saute ni ne duplique aucune ligne.
- `test_read_contract` — étendu à `/events`, `/systems/funnel` et aux nouveaux
  champs de `portfolio`.
- Frontend : fusion live/historique — même `event_id` reçu par WS et par backfill
  produit une seule entrée.

## Configuration ajoutée

Tous avec les défauts actuels, donc aucun changement de comportement au
déploiement :

```
HAIKU_ESCALATE_SCORE=60           # nouveau (seul seuil non câblé)
KRAKEN_READ_API_KEY=              # nouveau
KRAKEN_READ_API_SECRET=           # nouveau
CMI_ACCOUNT_POLL_S=60             # nouveau
CMI_EVENTS_RETENTION_MARKET_D=7   # nouveau
CMI_EVENTS_RETENTION_SIGNAL_D=90  # nouveau
```

Déjà existants, à exposer dans `docker-compose.vps.yml` pour être ajustables
sans rebuild : `DECISION_THRESHOLD`, `RISK_MIN_SCORE`, `RISK_MIN_CONFIDENCE`.

## Ordre de livraison

Trois incréments indépendamment déployables :

1. **Phase 1a + 1b** — instrumentation et seuils configurables. Ne change rien au
   comportement mais produit l'entonnoir. Seul livrable appelant une décision de
   l'opérateur : observer les chiffres, puis fixer les seuils. **1c suit, informé
   par ce que l'entonnoir montre.**
2. **Phase 2** — Kraken. Indépendante de la phase 1 : le vrai solde s'affiche même
   si le pipeline ne produit encore aucune décision.
3. **Phase 3** — persistance du flux. Bénéficie des deux précédentes : l'archive
   contient alors des décisions réelles et des snapshots de compte.

Une seule dépendance dure : la phase 3 archive `AccountSnapshotEvent`, donc le
topic de la phase 2 doit exister d'abord. Les phases 1 et 2 sont mutuellement
indépendantes.

## Hors périmètre

**Market Intelligence vide.** Cette page lit `/market/*` et `/data/*`, donc
l'ingestion — un problème distinct de la chaîne de décision. L'entonnoir de la
phase 1 dira si les collecteurs alimentent réellement le bus ; si le compte
d'événements entrants est bas, cela fera l'objet d'un spec séparé.
