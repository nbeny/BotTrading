# Diagrammes de flux & exemples d'événements

## Flux principal (end-to-end)

```
                 poll HTTP
 CoinGecko  ─────────────────►  collector-coingecko ──► market.price.events
 DexScreener ────────────────►  collector-dexscreener ─► market.dex.events
 CryptoCompare ──────────────►  collector-cryptocompare ► market.news.events
 Reddit ─────────────────────►  collector-reddit ─────► market.social.events

 market.news.events ┐
 market.social.events ┴──────►  sentiment-service ────► market.sentiment.events

 price/volume/dex/news/social/sentiment ─► ai-worker-haiku
                                                │  (corrélation par symbole,
                                                │   score rapide 0-100)
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
                                                risk-engine
                                          (SL/TP, exposition, blacklist)
                                                     │
                                                     ▼
                                          risk.approved.events ──► moteur de trading
```

## Séquence de corrélation (un token)

```
t0  PriceEvent(SOL, +8%/24h)            ─► haiku feature store: {price_change_24h:+8}
t1  VolumeEvent(SOL, spike x4)          ─► features: {volume_spike_ratio:4}
t2  SocialEvent(SOL, mentions +120%)    ─► features: {social_growth:1.2}
t3  SentimentEvent(SOL, +0.7)           ─► features: {sentiment_score:0.7}
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
