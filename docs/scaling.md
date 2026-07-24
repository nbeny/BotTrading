# Stratégie de scaling

## Principe : scaling horizontal par consumer group

Chaque service processeur (`haiku`, `sonnet`, `decision`, `risk`, `persister`,
`websocket-gateway`) est un **consumer group** Kafka. Le parallélisme maximal d'un groupe
= nombre de **partitions** du topic. Pour scaler, on ajoute des réplicas jusqu'à
`#replicas ≤ #partitions`. Au-delà, on augmente d'abord les partitions.

Deux services échappent au modèle « un consumer group par topic » :
- **sentiment-service** ne consomme pas Kafka — il boucle sur Postgres `raw_content`
  (lignes non scorées). On le scale en réplicas ; les lignes prises par batch sont
  marquées `scored_at` avant publication, ce qui borne le double-scoring.
- **trading-engine** consomme `control.commands` avec un **consumer group unique par
  réplica** afin que *chaque* instance applique *toutes* les commandes (mode, kill,
  caps), tandis que `risk.approved.events` reste un groupe partagé (une seule instance
  exécute chaque signal).

```
market.analysis.events (6 partitions)
   ├─ ai-worker-sonnet #1  → partitions 0,1
   ├─ ai-worker-sonnet #2  → partitions 2,3
   └─ ai-worker-sonnet #3  → partitions 4,5     (3 réplicas = équilibre optimal)
```

## Dimensionnement des topics

| Topic                    | Partitions | Justification                                  |
| ------------------------ | ---------- | ---------------------------------------------- |
| market.price.events      | 12         | fort volume (top ~200 coins × poll fréquent)   |
| market.dex.events        | 12         | très fort volume (nouveaux pools multi-chaînes)|
| market.volume/news/social/sentiment/analysis | 6 | volume moyen                        |
| decision.events          | 3          | faible volume (seuls les signaux forts)        |
| risk.approved.events     | 3          | faible volume (signaux finaux)                 |
| execution.events         | 3          | faible volume (résultats d'exécution)          |
| control.commands         | 3          | faible volume ; partition unique (`"control"`) → ordre total des commandes |

`market.news.events` / `market.social.events` restent définis (6 partitions) mais ne sont
plus alimentés : l'ingestion sociale/news passe désormais par Postgres `raw_content`.

Clé de partition = **symbole** ⇒ tous les événements d'un token vont sur la même
partition ⇒ ordre garanti et corrélation locale sans coordination globale.

## Goulots d'étranglement & réponses

| Goulot                              | Réponse                                                        |
| ----------------------------------- | ------------------------------------------------------------- |
| Quotas API providers                | rate-limiting distribué (Redis token bucket) + cache + backoff |
| Coût IA (tokens Claude)             | pipeline en entonnoir : HF (massif, gratuit) → Haiku (cheap) → Sonnet (rare) via `escalate` |
| Débit sentiment (modèle lourd)      | réplicas GPU/CPU + batching + fallback lexical                |
| Écritures TimescaleDB               | hypertables + compression + rétention ; writes idempotents (`on_conflict_do_nothing`) |
| État partagé                        | Redis (feature store, exposition, blacklist) — pas d'état en mémoire |

## Entonnoir de coût (clé de l'économie du système)

```
  100%   news/social/market  ──► sentiment-service (HuggingFace, coût ~0)
   ~30%  signaux corrélés     ──► ai-worker-haiku   (Claude Haiku, coût faible)
    ~5%  score ≥ 75 (escalate)──► ai-worker-sonnet  (Claude Sonnet, coût élevé, rare)
    ~1%  décisions            ──► risk-engine        (déterministe, coût ~0)
```

Le seuil `ANTHROPIC_ESCALATION_THRESHOLD` règle directement le budget Sonnet.

## Résilience

- **At-least-once** + handlers idempotents ⇒ un réplica qui meurt ne perd rien
  (offset non commit → rejoué par un autre membre du groupe).
- **Collectors idempotents** : ingestion dédupliquée par `(source, external_id)` (contrainte
  unique `raw_content`) ⇒ redémarrages sans doublons ; rate-limits token-bucket en Redis.
- **État runtime bot en Redis** (`trading:runtime/positions*/pending*`) ⇒ le trading-engine
  redémarre sans perdre positions ni mode ; il en est le seul writer.
- **Backpressure naturel** : si un consumer group ralentit, le lag Kafka monte
  (visible dans Prometheus) sans perdre d'événements ; on ajoute des réplicas.
- **Isolation des pannes** : la mort d'un provider (ex. Reddit) n'impacte pas les
  autres flux ; le système dégrade gracieusement.

## Migration vers l'orchestration (au-delà de Compose)

Le design est *cloud-native ready* : chaque service est une image sans état
persistant local. Passage à Kubernetes = `Deployment` + `HorizontalPodAutoscaler`
piloté sur le **consumer lag** Kafka (KEDA `kafka` scaler), sans changement de code.
```
