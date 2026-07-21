/** REST/domain models consumed by the terminal (portfolio, trading, risk). */

export type TradingMode = 'paper' | 'live';

export interface Portfolio {
  total_value_usd: number;
  cash_usd: number;
  kraken_balance_usd: number;
  invested_usd: number;
  unrealized_pnl_usd: number;
  unrealized_pnl_pct: number;
  realized_pnl_24h_usd: number;
  pnl_24h_pct: number;
  updated_at: string;
}

export interface Position {
  position_id: string;
  symbol: string;
  direction: 'long' | 'short';
  quantity: number;
  entry_price: number;
  current_price: number;
  value_usd: number;
  unrealized_pnl_usd: number;
  unrealized_pnl_pct: number;
  stop_loss: number | null;
  take_profit: number | null;
  protected: boolean;
  opened_at: string;
  mode: TradingMode;
}

export interface Trade {
  trade_id: string;
  symbol: string;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  price: number;
  quantity: number;
  cost_usd: number;
  fee_usd: number;
  pnl_usd: number | null;
  status: 'filled' | 'partial' | 'rejected' | 'pending';
  mode: TradingMode;
  executed_at: string;
}

/** A ranked opportunity awaiting operator validation. */
export interface Opportunity {
  opportunity_id: string;
  symbol: string;
  direction: 'long' | 'short';
  opportunity_score: number;
  confidence: number;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  position_size_pct: number;
  risk_reward_ratio: number;
  rationale: string;
  key_risks: string[];
  ai_validated: boolean;
  source: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  created_at: string;
}

export interface MarketToken {
  symbol: string;
  coin_id: string;
  name: string;
  price_usd: number;
  price_change_pct_24h: number;
  volume_24h_usd: number;
  liquidity_usd: number;
  market_cap_usd: number;
  sentiment_score: number;
  opportunity_score: number;
  is_trending: boolean;
  updated_at: string;
}

export interface NewsItem {
  id: string;
  title: string;
  url: string;
  source: string;
  symbols: string[];
  sentiment: number;
  published_at: string;
}

/** Claude worker decision surfaced in the intelligence dashboard. */
export interface WorkerDecision {
  id: string;
  symbol: string;
  worker: 'haiku' | 'sonnet';
  decision: string;
  opportunity_score: number;
  confidence: number;
  justification: string;
  escalated: boolean;
  created_at: string;
}

export interface RiskExposure {
  total_exposure_usd: number;
  total_exposure_pct: number;
  max_exposure_pct: number;
  by_asset: AssetExposure[];
  protected_positions: number;
  open_positions: number;
  daily_loss_usd: number;
  daily_loss_limit_usd: number;
  updated_at: string;
}

export interface AssetExposure {
  symbol: string;
  exposure_usd: number;
  exposure_pct: number;
  limit_pct: number;
  protected: boolean;
}

export interface RiskLimit {
  key: string;
  label: string;
  value: number;
  max: number;
  unit: string;
  breached: boolean;
}

export type AlertLevel = 'info' | 'warning' | 'critical';

export interface RiskAlert {
  id: string;
  level: AlertLevel;
  symbol?: string;
  message: string;
  created_at: string;
}

export interface PricePoint {
  t: string;
  price: number;
  volume?: number;
}

export interface TradingStatus {
  auto_trading_enabled: boolean;
  mode: TradingMode;
  updated_at: string;
}
