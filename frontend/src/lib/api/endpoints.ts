import { api, control } from './client';
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
  Trade,
  TradingMode,
  TradingStatus,
  WorkerDecision,
} from '@/lib/types/domain';
import type { AnalysisEvent, DecisionEvent, PriceEvent, SentimentEvent } from '@/lib/types/events';

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

// ── Risk ──────────────────────────────────────────────────────────────────────
export const riskApi = {
  exposure: () => api.get<RiskExposure>('/risk/exposure').then((r) => r.data),
  limits: () => api.get<RiskLimit[]>('/risk/limits').then((r) => r.data),
  alerts: (limit = 30) =>
    api.get<RiskAlert[]>('/risk/alerts', { params: { limit } }).then((r) => r.data),
};
