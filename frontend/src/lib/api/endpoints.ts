import { api, control } from './client';
import type { ContentPage, DataStats, DecisionTrace, ContentQuery } from '@/lib/types/content';
import type { DecisionExplain } from '@/lib/types/explain';
import type {
  JournalAttribution,
  JournalCalibration,
  JournalDecisionsPage,
  JournalWindow,
} from '@/lib/types/journal';
import type { MarketRegime } from '@/lib/types/regime';
import type {
  EngineCaps,
  MarketToken,
  NewsItem,
  Opportunity,
  Portfolio,
  Position,
  PricePoint,
  RiskAlert,
  RiskExposure,
  RiskLimit,
  TokenDossier,
  Trade,
  TradingMode,
  TradingStatus,
  WorkerDecision,
} from '@/lib/types/domain';
import type {
  AnalysisEvent,
  DecisionEvent,
  EventPage,
  PriceEvent,
  SentimentEvent,
} from '@/lib/types/events';
import type { FunnelStats, StageDetail, SystemsSnapshot, SystemsWindow } from '@/lib/types/systems';

// ── Portfolio ───────────────────────────────────────────────────────────────
export const portfolioApi = {
  get: () => api.get<Portfolio>('/portfolio').then((r) => r.data),
  positions: () => api.get<Position[]>('/portfolio/positions').then((r) => r.data),
  trades: (limit = 50) =>
    api.get<Trade[]>('/portfolio/trades', { params: { limit } }).then((r) => r.data),
  history: (range = '30d') =>
    api.get<PricePoint[]>('/portfolio/history', { params: { range } }).then((r) => r.data),
};

// ── Market intelligence ───────────────────────────────────────────────────────
export const marketApi = {
  tokens: () => api.get<MarketToken[]>('/market/tokens').then((r) => r.data),
  token: (symbol: string) =>
    api.get<MarketToken>(`/market/tokens/${symbol}`).then((r) => r.data),
  prices: (symbol: string, range = '1d') =>
    api
      .get<PricePoint[]>(`/market/tokens/${symbol}/prices`, { params: { range } })
      .then((r) => r.data),
  dossier: (symbol: string) =>
    api
      .get<TokenDossier>(`/market/tokens/${symbol}/dossier`)
      .then((r) => r.data),
  news: (limit = 20) =>
    api.get<NewsItem[]>('/market/news', { params: { limit } }).then((r) => r.data),
  decisions: (limit = 30) =>
    api
      .get<WorkerDecision[]>('/market/decisions', { params: { limit } })
      .then((r) => r.data),
  signals: (limit = 30) =>
    api
      .get<(PriceEvent | AnalysisEvent | SentimentEvent | DecisionEvent)[]>('/market/signals', {
        params: { limit },
      })
      .then((r) => r.data),
};

// ── Event archive ─────────────────────────────────────────────────────────────
// Cursor-paginated history of the raw broadcast stream (api-gateway persists
// every WS frame into TimescaleDB). `before` is the opaque `next_cursor` of the
// previous page; `types` is a comma-separated list of event types.
export const eventsApi = {
  page: (params: { limit?: number; before?: string | null; types?: string; symbol?: string }) =>
    api
      .get<EventPage>('/events', {
        params: {
          limit: params.limit ?? 100,
          ...(params.before ? { before: params.before } : {}),
          ...(params.types ? { types: params.types } : {}),
          ...(params.symbol ? { symbol: params.symbol } : {}),
        },
      })
      .then((r) => r.data),
};

// ── Trading control ───────────────────────────────────────────────────────────
export interface ManualOrderInput {
  symbol: string;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  quantity: number;
  price?: number;
}

export interface AdjustSlTpInput {
  stop_loss?: number | null;
  take_profit?: number | null;
}

// All trading control routes are served by control-api (via the `control`
// client), which publishes ControlCommandEvents to Kafka. Reads above stay on
// the read-only api-gateway (`api`).
export const tradingApi = {
  status: () => control.get<TradingStatus>('/trading/status').then((r) => r.data),
  setAutoTrading: (enabled: boolean) =>
    control.post<TradingStatus>('/trading/auto', { enabled }).then((r) => r.data),
  setMode: (mode: TradingMode) =>
    control.post<TradingStatus>('/trading/mode', { mode }).then((r) => r.data),

  opportunities: (status: 'pending' | 'all' = 'pending') =>
    control
      .get<Opportunity[]>('/trading/opportunities', { params: { status } })
      .then((r) => r.data),
  approveOpportunity: (id: string) =>
    control.post<Opportunity>(`/trading/opportunities/${id}/approve`).then((r) => r.data),
  rejectOpportunity: (id: string, reason?: string) =>
    control
      .post<Opportunity>(`/trading/opportunities/${id}/reject`, { reason })
      .then((r) => r.data),

  placeOrder: (input: ManualOrderInput) =>
    control.post<Trade>('/trading/orders', input).then((r) => r.data),
  closePosition: (positionId: string) =>
    control.post<Trade>(`/trading/positions/${positionId}/close`).then((r) => r.data),
  adjustSlTp: (positionId: string, input: AdjustSlTpInput) =>
    control
      .patch<Position>(`/trading/positions/${positionId}/sltp`, input)
      .then((r) => r.data),
};

// ── Engine settings ─────────────────────────────────────────────────────────────
export const settingsApi = {
  status: () => control.get<TradingStatus>('/trading/status').then((r) => r.data),
  setMode: (mode: TradingMode) => control.post('/trading/mode', { mode }).then((r) => r.data),
  setKill: (enabled: boolean) => control.post('/trading/kill', { enabled }).then((r) => r.data),
  setAuto: (enabled: boolean) => control.post('/trading/auto', { enabled }).then((r) => r.data),
  setCaps: (caps: Partial<EngineCaps>) => control.post('/trading/caps', caps).then((r) => r.data),
};

// ── Collector sources (operator toggles) + AI quota status ─────────────────────

/** Health of one collector platform, published by the provider itself.
 *
 *  Three things about this shape are easy to get wrong in a renderer:
 *  - `reason` is populated on green paths too — `ok: true` ships with
 *    "no channels configured" or "rate-limited by Telegram, resuming in 42s".
 *    A non-null `reason` is **not** a fault; `ok` is the fault indicator.
 *  - `ok` means "the source is contributing", not "the last cycle completed".
 *  - `channels` maps a written-off handle to why it contributes nothing
 *    (usually an operator typo). It is the only feedback the operator gets
 *    that a handle they typed is wrong.
 *  - `updated_at` exists so a frozen key is detectable: the key is durable and
 *    the provider stops writing entirely while the platform is switched off. */
export interface SourceStatus {
  ok: boolean;
  reason: string | null;
  channels: Record<string, string>;
  updated_at: string;
}

export interface CollectorRuntime {
  social_enabled: boolean;
  news_enabled: boolean;
  platforms: Record<string, boolean>;
  known_platforms: { social: string[]; news: string[] };
  /** Channels the Telegram provider polls; re-read from Redis every cycle. */
  telegram_channels: string[];
  /** Keyed by platform. A platform that has never reported is **absent** —
   *  that is "unknown", not "healthy", and must never render as green. */
  source_status: Record<string, SourceStatus>;
}

export interface AiQuotaStatus {
  paused: boolean;
  resume_at: number | null;
  workers: { service: string; paused: boolean; resume_at?: number }[];
}

export interface Coverage {
  sentiment: { total: number; scored: number; unscored: number };
  escalations: { pending: number };
}

export const collectorsApi = {
  runtime: () => control.get<CollectorRuntime>('/collectors/runtime').then((r) => r.data),
  setRuntime: (patch: {
    social_enabled?: boolean;
    news_enabled?: boolean;
    platforms?: Record<string, boolean>;
    /** Replaces the list wholesale — send the complete array, never a delta.
     *  `[]` is accepted and means "poll nobody"; omit the field to leave the
     *  list untouched. The server normalizes, dedupes, and 422s on an invite
     *  link, a blank entry, or more than 50 channels. */
    telegram_channels?: string[];
  }) => control.post<CollectorRuntime>('/collectors/runtime', patch).then((r) => r.data),
  aiQuota: () => control.get<AiQuotaStatus>('/systems/ai/quota').then((r) => r.data),
  coverage: () => control.get<Coverage>('/systems/coverage').then((r) => r.data),
  backfill: () =>
    control
      .post<{ requeued: number; candidates: number }>('/systems/backfill')
      .then((r) => r.data),
};

// ── Systems / infrastructure observability ─────────────────────────────────────
// Read-only aggregate of every service's health/metrics. In live mode this maps
// to the api-gateway (or a dedicated observability endpoint); on mock it hits the
// built-in BFF snapshot generator.
export const systemsApi = {
  overview: (window: SystemsWindow = '24h') =>
    api.get<SystemsSnapshot>(`/systems/overview?window=${window}`).then((r) => r.data),
  funnel: (window: SystemsWindow = '24h') =>
    api.get<FunnelStats>(`/systems/funnel?window=${window}`).then((r) => r.data),
  stage: (id: string, window: SystemsWindow = '24h', limit = 20) =>
    api
      .get<StageDetail>(`/systems/stage/${id}?window=${window}&limit=${limit}`)
      .then((r) => r.data),
};

// ── Decision trace ────────────────────────────────────────────────────────────
export const traceApi = {
  get: (cid: string) => api.get<DecisionTrace>(`/trace/${cid}`).then((r) => r.data),
};

// ── Data explorer ─────────────────────────────────────────────────────────────
export const dataApi = {
  content: (q: ContentQuery = {}) =>
    api.get<ContentPage>('/data/content', { params: q }).then((r) => r.data),
  stats: () => api.get<DataStats>('/data/stats').then((r) => r.data),
};

// ── Risk ──────────────────────────────────────────────────────────────────────
export const riskApi = {
  exposure: () => api.get<RiskExposure>('/risk/exposure').then((r) => r.data),
  limits: () => api.get<RiskLimit[]>('/risk/limits').then((r) => r.data),
  alerts: (limit = 30) =>
    api.get<RiskAlert[]>('/risk/alerts', { params: { limit } }).then((r) => r.data),
};

// ── Market regime ─────────────────────────────────────────────────────────────
export const regimeApi = {
  get: () => api.get<MarketRegime>('/market/regime').then((r) => r.data),
};

// ── Decision explain ──────────────────────────────────────────────────────────
export const explainApi = {
  get: (id: string) => api.get<DecisionExplain>(`/decisions/${id}/explain`).then((r) => r.data),
};

// ── Journal ───────────────────────────────────────────────────────────────────
export const journalApi = {
  decisions: (window: JournalWindow = '30d', limit = 50, offset = 0) =>
    api
      .get<JournalDecisionsPage>('/systems/journal/decisions', { params: { window, limit, offset } })
      .then((r) => r.data),
  calibration: (threshold: number, window: JournalWindow = '30d') =>
    api
      .get<JournalCalibration>('/systems/journal/calibration', { params: { window, threshold } })
      .then((r) => r.data),
  attribution: (window: JournalWindow = '30d') =>
    api
      .get<JournalAttribution>('/systems/journal/attribution', { params: { window } })
      .then((r) => r.data),
};
