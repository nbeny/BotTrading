# websocket-gateway

Bridges Kafka events to browser WebSocket clients — the real-time feed behind the
Next.js operator terminal.

```
Kafka topics ──▶ BroadcastConsumer (aiokafka) ──▶ ConnectionManager ──▶ browser WS clients
```

## Endpoints

| Method    | Path       | Description                                             |
| --------- | ---------- | ------------------------------------------------------- |
| GET       | `/health`  | Liveness + readiness (shared `create_app` factory).     |
| GET       | `/metrics` | Prometheus metrics (shared factory).                    |
| WEBSOCKET | `/ws`      | Live event stream. Optional `?token=<jwt>` auth hook.   |

## Frame schema

Every consumed Kafka message is broadcast to all connected clients as a single
JSON text frame:

```json
{
  "topic": "market.price.events",
  "event": { "event_type": "PriceEvent", "symbol": "BTC", "...": "..." },
  "ts": "2026-07-21T12:34:56.789012+00:00"
}
```

- `topic` — the raw Kafka topic the event came from.
- `event` — the decoded event object (JSON of the Kafka value).
- `ts` — ISO-8601 UTC timestamp of when the gateway emitted the frame.

## Consumed topics

`market.price.events`, `market.volume.events`, `market.dex.events`,
`market.news.events`, `market.social.events`, `market.sentiment.events`,
`market.analysis.events`, `decision.events`, `risk.approved.events`
(the `Topic` enum values: PRICE, VOLUME, DEX, NEWS, SOCIAL, SENTIMENT,
ANALYSIS, DECISION, RISK_APPROVED).

The consumer uses `group_id=websocket-gateway` and `auto_offset_reset=latest`,
so clients only receive events produced while the gateway is running.

## Environment variables

| Variable                  | Default      | Purpose                                                        |
| ------------------------- | ------------ | -------------------------------------------------------------- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka brokers (from the shared `KafkaSettings`).               |
| `LOG_LEVEL`               | `INFO`       | Log level.                                                     |
| `JWT_SECRET`              | *(unset)*    | If set, `/ws` tokens are HS256-verified; otherwise decoded best-effort (dev). |

Auth behavior on `/ws`:
- **`JWT_SECRET` set** — the `token` query param is required and its HS256
  signature is verified; a missing/invalid token closes the socket with code
  1008.
- **`JWT_SECRET` unset** — any token (or none) is accepted; if a token is
  present its `sub`/`role` claims are decoded for logging only.

## Frontend connection (Next.js terminal)

Direct (local dev, via the exposed host port):

```env
NEXT_PUBLIC_WS_URL=ws://localhost:8080/ws
```

Via Traefik (TLS):

```env
NEXT_PUBLIC_WS_URL=wss://ws.cmi.localhost/ws
```

Example client:

```ts
const ws = new WebSocket(`${process.env.NEXT_PUBLIC_WS_URL}?token=${jwt}`);
ws.onmessage = (e) => {
  const { topic, event, ts } = JSON.parse(e.data);
  // dispatch(topic, event, ts)
};
```

## Local run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or via docker-compose as the `websocket-gateway` service (published on host
port `8080`, Traefik host `ws.cmi.localhost`).
