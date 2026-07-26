/**
 * Real-time event types mirroring the backend Pydantic schemas in
 * `libs/cmi_common/cmi_common/events`. These arrive over the WebSocket gateway
 * (Kafka -> WebSocket Service -> Next.js) and match the JSON examples in
 * `docs/flows.md`.
 */

/** Envelope fields shared by every CMI event. */
export interface EventBase {
  event_id?: string;
  event_type: EventType;
  schema_version?: number;
  occurred_at?: string;
  source: string;
  correlation_id?: string;
  symbol: string;
}

export type EventType =
  | 'PriceEvent'
  | 'VolumeEvent'
  | 'DexEvent'
  | 'NewsEvent'
  | 'SocialEvent'
  | 'SentimentEvent'
  | 'AnalysisEvent'
  | 'DecisionEvent'
  | 'RiskApprovedEvent'
  | 'OrderExecutedEvent'
  | 'PositionChangedEvent'
  | 'PortfolioChangedEvent';

/** market.price.events */
export interface PriceEvent extends EventBase {
  event_type: 'PriceEvent';
  coin_id?: string;
  price_usd: string;
  market_cap_usd?: string;
  volume_24h_usd?: string;
  price_change_pct_24h: number;
  market_cap_rank?: number;
  is_trending?: boolean;
}

/** market.volume.events */
export interface VolumeEvent extends EventBase {
  event_type: 'VolumeEvent';
  volume_24h_usd: string;
  volume_spike_ratio?: number;
}

/** market.dex.events */
export interface DexEvent extends EventBase {
  event_type: 'DexEvent';
  liquidity_usd?: string;
  volume_24h_usd?: string;
  price_usd?: string;
  dex?: string;
  pair_address?: string;
}

/** market.news.events */
export interface NewsEvent extends EventBase {
  event_type: 'NewsEvent';
  title: string;
  url?: string;
  body?: string;
  published_at?: string;
}

/** market.social.events */
export interface SocialEvent extends EventBase {
  event_type: 'SocialEvent';
  platform?: string;
  mentions?: number;
  social_growth?: number;
}

/** market.sentiment.events */
export interface SentimentEvent extends EventBase {
  event_type: 'SentimentEvent';
  sentiment_score: number;
  confidence: number;
  model_name: string;
  input_kind: 'social' | 'news' | string;
  sample_size: number;
}

/** market.analysis.events — Claude Haiku triage */
export interface AnalysisEvent extends EventBase {
  event_type: 'AnalysisEvent';
  opportunity_score: number;
  confidence: number;
  reason: string;
  price_change_pct_24h?: number;
  volume_spike_ratio?: number;
  sentiment_score?: number;
  social_growth?: number;
  escalate: boolean;
}

/** decision.events — Claude Sonnet senior validation / deterministic engine */
export interface DecisionEvent extends EventBase {
  event_type: 'DecisionEvent';
  direction: 'long' | 'short';
  opportunity_score: number;
  confidence: number;
  rationale: string;
  key_risks: string[];
  ai_validated: boolean;
}

/** risk.approved.events — Risk engine sizing & protection */
export interface RiskApprovedEvent extends EventBase {
  event_type: 'RiskApprovedEvent';
  direction: 'long' | 'short';
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  confidence: number;
  position_size_pct: number;
  risk_reward_ratio: number;
}

/** Emitted by the trading engine once an order hits Kraken. */
export interface OrderExecutedEvent extends EventBase {
  event_type: 'OrderExecutedEvent';
  order_id: string;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  price: number;
  volume: number;
  cost: number;
  fee?: number;
  status: 'filled' | 'partial' | 'rejected';
  mode: 'dry_run' | 'demo' | 'live';
}

/** Position lifecycle change. */
export interface PositionChangedEvent extends EventBase {
  event_type: 'PositionChangedEvent';
  position_id: string;
  change: 'opened' | 'updated' | 'closed';
  quantity: number;
  avg_price: number;
  unrealized_pnl?: number;
}

/** Portfolio total value variation. */
export interface PortfolioChangedEvent extends EventBase {
  event_type: 'PortfolioChangedEvent';
  total_value_usd: number;
  cash_usd: number;
  pnl_24h_pct: number;
}

export type CmiEvent =
  | PriceEvent
  | VolumeEvent
  | DexEvent
  | NewsEvent
  | SocialEvent
  | SentimentEvent
  | AnalysisEvent
  | DecisionEvent
  | RiskApprovedEvent
  | OrderExecutedEvent
  | PositionChangedEvent
  | PortfolioChangedEvent;

/**
 * A row of the server-side event archive (`GET /events`). The api-gateway
 * persists the raw broadcast stream into TimescaleDB, so the feed survives a
 * page reload — unlike the rolling in-memory buffer of the WebSocket provider.
 *
 * `event_type` is a plain `string` (not `EventType`): the archive stores
 * whatever the bus carried, including event types this client does not know.
 */
export interface ArchivedEvent {
  time: string;
  event_id: string;
  event_type: string;
  topic: string;
  symbol: string | null;
  correlation_id: string | null;
  payload: Record<string, unknown>;
}

/** Cursor-paginated archive page. `next_cursor` is opaque — round-trip it as-is. */
export interface EventPage {
  items: ArchivedEvent[];
  next_cursor: string | null;
}

/** Wrapper the WebSocket gateway pushes: `{ topic, event }`. */
export interface WsMessage<T extends CmiEvent = CmiEvent> {
  topic: string;
  event: T;
  ts: string;
}
