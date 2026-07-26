# Diagrammes de flux & exemples d'événements

## Flux principal (end-to-end)

```
                 poll HTTP (Kafka)
 CoinGecko  ─────────────────►  collector-coingecko ──► market.price/volume.events
 DexScreener ────────────────►  collector-dexscreener ─► market.dex.events

                 poll HTTP par plateforme (fan-out, pas de cascade)
 Bluesky/Reddit/Mastodon/4chan/Farcaster/YouTube/Lens ─► collector-social ─┐
 CryptoCompare/RSS/GDELT/NewsData ────────────────────► collector-news ────┤
                                                                           ▼
                                                         Postgres  raw_content
                                                                           │  (scored_at IS NULL)
                                                         sentiment-service ─┤
                                                         (HF, score le DB,  │
                                                          upsert agg)       ▼
                                                              market.sentiment.events

 price/volume/dex/sentiment ─► ai-worker-haiku
                                     │  (corrélation par symbole, score rapide 0-100)
                                     ▼
                            market.analysis.events
                              │                 │
               escalate=true  │                 │ (toujours)
                              ▼                 ▼
                      ai-worker-sonnet    decision-engine
                      (validation IA)     (scoring déterministe)
                              │                 │
                              └──────► decision.events ◄──┘
                                          │
                                          ▼
                                     risk-engine (SL/TP, exposition, blacklist)
                                          │
                                          ▼
                                 risk.approved.events ──► trading-engine (Kraken Futures)
                                                               │
                                                               ▼
                                                        execution.events
```

## Plan de contrôle (opérateur)

```
 web-terminal ─REST /trading/*─► control-api ─► control.commands ─► trading-engine
   (JWT)                          (aucune écriture DB)      (applique + mute Redis trading:*)

 trading-engine ─► execution.events ┐
 market/decision/risk topics ───────┴► websocket-gateway ─WS /ws?token=─► web-terminal
 api-gateway (Kafka→Postgres) ─REST GET /api/v1/*─► web-terminal (lecture)
```

## Séquence de corrélation (un token)

```
t0  PriceEvent(SOL, +8%/24h)            ─► haiku feature store: {price_change_24h:+8}
t1  VolumeEvent(SOL, spike x4)          ─► features: {volume_spike_ratio:4}
t2  DexEvent(SOL, liquidité +)          ─► features: {liquidity_usd:...}
t3  SentimentEvent(SOL, +0.7)           ─► features: {sentiment_score:0.7}
    (SentimentEvent agrège l'ingestion sociale/news scorée depuis raw_content)
       ↓ (haiku a market + signal → analyse)
    AnalysisEvent(SOL, score=82, escalate=true)
       ↓                                   ↓
    decision-engine                     ai-worker-sonnet
    score déterministe=78               validation senior → DecisionEvent(ai_validated)
       ↓                                   ↓
                 DecisionEvent(SOL) ──► risk-engine
                                          entry=150, SL=142.5, TP=165, RR=3.0
                                          ↓
                             RiskApprovedEvent(SOL, conf=0.87)
```

## Exemples d'événements (JSON)

### `market.price.events` — PriceEvent
```json
{
  "event_id": "3f2a...","event_type": "PriceEvent","schema_version": 1,
  "occurred_at": "2026-07-21T10:00:00Z","source": "coingecko",
  "correlation_id": "c-9981","symbol": "SOL","coin_id": "solana",
  "price_usd": "150.20","market_cap_usd": "68400000000",
  "volume_24h_usd": "3200000000","price_change_pct_24h": 8.1,
  "market_cap_rank": 5,"is_trending": true
}
```

### `market.sentiment.events` — SentimentEvent
```json
{
  "event_type": "SentimentEvent","source": "sentiment-service",
  "symbol": "SOL","sentiment_score": 0.72,"confidence": 0.9,
  "model_name": "ElKulako/cryptobert","input_kind": "social","sample_size": 42
}
```

### `market.analysis.events` — AnalysisEvent
```json
{
  "event_type": "AnalysisEvent","source": "ai-worker-haiku","symbol": "ETH",
  "opportunity_score": 82,"confidence": 0.78,
  "reason": "Positive news combined with increasing social activity",
  "price_change_pct_24h": 5.4,"volume_spike_ratio": 3.1,
  "sentiment_score": 0.6,"social_growth": 0.9,"escalate": true
}
```

### `decision.events` — DecisionEvent
```json
{
  "event_type": "DecisionEvent","source": "ai-worker-sonnet","symbol": "SOL",
  "direction": "long","opportunity_score": 84,"confidence": 0.85,
  "rationale": "Strong momentum, healthy liquidity, corroborating sentiment",
  "key_risks": ["recent volatility","concentration on a single DEX"],
  "ai_validated": true
}
```

### `risk.approved.events` — RiskApprovedEvent
```json
{
  "event_type": "RiskApprovedEvent","source": "risk-engine","symbol": "SOL",
  "direction": "long","entry_price": 150,"stop_loss": 142,"take_profit": 165,
  "confidence": 0.87,"position_size_pct": 0.043,"risk_reward_ratio": 1.87
}
```

### `control.commands` — ControlCommandEvent (opérateur → trading-engine)
```json
{
  "event_type": "ControlCommandEvent","source": "control-api",
  "command": "set_mode","payload": { "mode": "demo" },"issued_by": "admin"
}
```

### `execution.events` — ExecutionEvent (trading-engine → api-gateway / WS)
```json
{
  "event_type": "ExecutionEvent","source": "trading-engine","kind": "filled",
  "symbol": "SOL","direction": "long","risk_event_id": "c-9981",
  "kraken_order_id": "OABC-123","fill_price": 150.2,"size": 1.5,"pnl": null
}
```

### `journal.entries` — JournalEntryEvent (ai-worker-sonnet → api-gateway)

Une ligne par analyse, escaladée ou non. Les non-escaladées sont le groupe
témoin : sans elles, « ce signal méritait-il un appel ? » est indécidable, parce
que la seule population observable serait celle que la porte a déjà retenue.
Persisté dans `decision_journal` (180 j) et **exclu** de l'archive brute — il a
déjà sa table, l'archiver doublerait la plus grosse du système.

### `account.snapshot.events` — AccountSnapshotEvent (trading-engine → api-gateway / WS)

Solde réel d'un venue à un instant, publié par le seul service détenteur des
secrets d'exchange. Clé de partition : le `venue`, pour que ses instantanés
restent ordonnés entre eux — un instantané périmé livré après un frais ferait
reculer le solde affiché. Persisté dans `account_snapshots` ; le plan de lecture
en sert le dernier, ou `null` avec `balance_source: "unavailable"` quand aucune
clé n'est configurée.
