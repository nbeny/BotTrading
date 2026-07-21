import { api } from './client';
import type {
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

export const tradingApi = {
  status: () => api.get<TradingStatus>('/trading/status').then((r) => r.data),
  setAutoTrading: (enabled: boolean) =>
    api.post<TradingStatus>('/trading/auto', { enabled }).then((r) => r.data),
  setMode: (mode: TradingMode) =>
    api.post<TradingStatus>('/trading/mode', { mode }).then((r) => r.data),

  opportunities: (status: 'pending' | 'all' = 'pending') =>
    api.get<Opportunity[]>('/trading/opportunities', { params: { status } }).then((r) => r.data),
  approveOpportunity: (id: string) =>
    api.post<Opportunity>(`/trading/opportunities/${id}/approve`).then((r) => r.data),
  rejectOpportunity: (id: string, reason?: string) =>
    api
      .post<Opportunity>(`/trading/opportunities/${id}/reject`, { reason })
      .then((r) => r.data),

  placeOrder: (input: ManualOrderInput) =>
    api.post<Trade>('/trading/orders', input).then((r) => r.data),
  closePosition: (positionId: string) =>
    api.post<Trade>(`/trading/positions/${positionId}/close`).then((r) => r.data),
  adjustSlTp: (positionId: string, input: AdjustSlTpInput) =>
    api
      .patch<Position>(`/trading/positions/${positionId}/sltp`, input)
      .then((r) => r.data),
};

// ── Risk ──────────────────────────────────────────────────────────────────────
export const riskApi = {
  exposure: () => api.get<RiskExposure>('/risk/exposure').then((r) => r.data),
  limits: () => api.get<RiskLimit[]>('/risk/limits').then((r) => r.data),
  alerts: (limit = 30) =>
    api.get<RiskAlert[]>('/risk/alerts', { params: { limit } }).then((r) => r.data),
};
