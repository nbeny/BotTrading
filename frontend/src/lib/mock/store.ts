/**
 * Module-level singleton in-memory store for the mock BFF.
 * Persists across Next.js route handler calls during a dev-server session.
 */
import type {
  AssetExposure,
  BalanceSource,
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
import { BY_SYMBOL, RISK_MESSAGES, UNIVERSE, pick, rand, reason, round, uid } from './universe';
import { currentPortfolioValue, currentPrice } from './sim';

// ── helpers ──────────────────────────────────────────────────────────────────

function isoNow(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

function hoursAgo(h: number): string {
  return isoNow(-h * 3600_000);
}

function daysAgo(d: number): string {
  return hoursAgo(d * 24);
}

// ── Trading status ────────────────────────────────────────────────────────────

let _status: TradingStatus = {
  auto_trading_enabled: false,
  trading_enabled: true,
  mode: 'dry_run',
  updated_at: isoNow(),
};

export function getTradingStatus(): TradingStatus {
  return { ..._status };
}

export function setAuto(enabled: boolean): TradingStatus {
  _status = { ..._status, auto_trading_enabled: enabled, updated_at: isoNow() };
  return getTradingStatus();
}

export function setKill(enabled: boolean): TradingStatus {
  // Kill switch engaged disables trading; releasing it re-enables trading.
  _status = { ..._status, trading_enabled: !enabled, updated_at: isoNow() };
  return getTradingStatus();
}

export function setMode(mode: TradingMode): TradingStatus {
  _status = { ..._status, mode, updated_at: isoNow() };
  return getTradingStatus();
}

// ── Positions ─────────────────────────────────────────────────────────────────

function makePosition(
  symbol: string,
  direction: 'long' | 'short',
  qty: number,
  entryPrice: number,
  currentPrice: number,
  hoursOpen: number,
  hasSL: boolean,
  hasTP: boolean,
): Position {
  const value = round(qty * currentPrice, 2);
  const costBasis = round(qty * entryPrice, 2);
  const pnl = direction === 'long' ? round(value - costBasis, 2) : round(costBasis - value, 2);
  const pnlPct = round((pnl / costBasis) * 100, 2);
  const sl = hasSL
    ? round(direction === 'long' ? entryPrice * 0.95 : entryPrice * 1.05, 2)
    : null;
  const tp = hasTP
    ? round(direction === 'long' ? entryPrice * 1.15 : entryPrice * 0.85, 2)
    : null;

  return {
    position_id: uid('pos'),
    symbol,
    direction,
    quantity: qty,
    entry_price: entryPrice,
    current_price: currentPrice,
    value_usd: value,
    unrealized_pnl_usd: pnl,
    unrealized_pnl_pct: pnlPct,
    stop_loss: sl,
    take_profit: tp,
    protected: hasSL && hasTP,
    opened_at: hoursAgo(hoursOpen),
    mode: 'dry_run',
  };
}

const BTC = BY_SYMBOL.get('BTC')!;
const ETH = BY_SYMBOL.get('ETH')!;
const SOL = BY_SYMBOL.get('SOL')!;
const AVAX = BY_SYMBOL.get('AVAX')!;

let _positions: Position[] = [
  makePosition('BTC', 'long', 0.12, 66800, BTC.price, 18, true, true),
  makePosition('ETH', 'long', 2.5, 3420, ETH.price, 6, true, false),
  makePosition('SOL', 'long', 40, 142, SOL.price, 3, false, false),
  makePosition('AVAX', 'short', 15, 41.2, AVAX.price, 1, true, true),
];

// Positions are re-priced against the simulation's live price on every read, so
// current_price, value and unrealized PnL actually move (the "PnL & positions
// live" panel ticks) instead of being frozen at build time.
function reprice(p: Position): Position {
  const cur = round(currentPrice(p.symbol), p.entry_price < 10 ? 4 : 2);
  const value = round(p.quantity * cur, 2);
  const cost = round(p.quantity * p.entry_price, 2);
  const pnl = round(p.direction === 'long' ? value - cost : cost - value, 2);
  const pnlPct = cost ? round((pnl / cost) * 100, 2) : 0;
  return { ...p, current_price: cur, value_usd: value, unrealized_pnl_usd: pnl, unrealized_pnl_pct: pnlPct };
}

export function getPositions(): Position[] {
  return _positions.map(reprice);
}

export function closePosition(id: string): Trade | null {
  const idx = _positions.findIndex((p) => p.position_id === id);
  if (idx === -1) return null;
  const pos = reprice(_positions[idx]);
  const trade: Trade = {
    trade_id: uid('trd'),
    symbol: pos.symbol,
    side: pos.direction === 'long' ? 'sell' : 'buy',
    order_type: 'market',
    price: pos.current_price,
    quantity: pos.quantity,
    cost_usd: round(pos.quantity * pos.current_price, 2),
    fee_usd: round(pos.quantity * pos.current_price * 0.001, 4),
    pnl_usd: pos.unrealized_pnl_usd,
    status: 'filled',
    mode: pos.mode,
    executed_at: isoNow(),
  };
  _positions = _positions.filter((_, i) => i !== idx);
  _trades.unshift(trade);
  return trade;
}

export function adjustSlTp(
  id: string,
  input: { stop_loss?: number | null; take_profit?: number | null },
): Position | null {
  const idx = _positions.findIndex((p) => p.position_id === id);
  if (idx === -1) return null;
  const pos = { ..._positions[idx] };
  if ('stop_loss' in input) pos.stop_loss = input.stop_loss ?? null;
  if ('take_profit' in input) pos.take_profit = input.take_profit ?? null;
  pos.protected = pos.stop_loss !== null && pos.take_profit !== null;
  _positions[idx] = pos;
  return { ...pos };
}

// ── Trades ─────────────────────────────────────────────────────────────────────

function makeTrade(
  symbol: string,
  side: 'buy' | 'sell',
  price: number,
  qty: number,
  status: Trade['status'],
  hoursBack: number,
  pnl: number | null = null,
): Trade {
  const cost = round(price * qty, 2);
  return {
    trade_id: uid('trd'),
    symbol,
    side,
    order_type: Math.random() > 0.4 ? 'market' : 'limit',
    price,
    quantity: qty,
    cost_usd: cost,
    fee_usd: round(cost * 0.001, 4),
    pnl_usd: pnl,
    status,
    mode: 'dry_run',
    executed_at: hoursAgo(hoursBack),
  };
}

let _trades: Trade[] = [
  makeTrade('BTC', 'buy', 66800, 0.12, 'filled', 18),
  makeTrade('ETH', 'buy', 3420, 2.5, 'filled', 6),
  makeTrade('SOL', 'buy', 142, 40, 'filled', 3),
  makeTrade('AVAX', 'sell', 41.2, 15, 'filled', 1),
  makeTrade('BTC', 'sell', 65200, 0.08, 'filled', 26, round(rand(-200, 400), 2)),
  makeTrade('ETH', 'buy', 3310, 1.0, 'filled', 30),
  makeTrade('ETH', 'sell', 3490, 1.0, 'filled', 28, round(rand(100, 300), 2)),
  makeTrade('SOL', 'sell', 158, 20, 'filled', 36, round(rand(50, 250), 2)),
  makeTrade('SOL', 'buy', 139, 20, 'filled', 40),
  makeTrade('LINK', 'buy', 17.2, 80, 'filled', 48),
  makeTrade('LINK', 'sell', 18.1, 80, 'filled', 45, round(rand(30, 120), 2)),
  makeTrade('DOGE', 'buy', 0.155, 2000, 'filled', 52),
  makeTrade('DOGE', 'sell', 0.168, 2000, 'filled', 50, round(rand(10, 60), 2)),
  makeTrade('ARB', 'buy', 1.08, 500, 'filled', 60),
  makeTrade('ARB', 'sell', 1.05, 500, 'rejected', 58, null),
  makeTrade('INJ', 'buy', 26.5, 15, 'filled', 70),
  makeTrade('INJ', 'sell', 28.2, 15, 'filled', 65, round(rand(15, 80), 2)),
  makeTrade('RNDR', 'buy', 9.2, 50, 'filled', 80),
  makeTrade('RNDR', 'sell', 10.1, 50, 'filled', 75, round(rand(30, 120), 2)),
  makeTrade('WIF', 'buy', 2.1, 200, 'filled', 90),
  makeTrade('WIF', 'sell', 2.4, 200, 'filled', 85, round(rand(20, 80), 2)),
  makeTrade('BTC', 'buy', 64800, 0.05, 'filled', 100),
  makeTrade('BTC', 'sell', 67100, 0.05, 'filled', 95, round(rand(100, 400), 2)),
  makeTrade('ETH', 'buy', 3200, 3.0, 'filled', 110),
  makeTrade('ETH', 'sell', 3500, 3.0, 'filled', 105, round(rand(200, 900), 2)),
  makeTrade('SOL', 'buy', 135, 30, 'partial', 120),
  makeTrade('AVAX', 'buy', 36.8, 20, 'filled', 130),
  makeTrade('AVAX', 'sell', 40.5, 20, 'filled', 125, round(rand(40, 180), 2)),
  makeTrade('LINK', 'buy', 16.5, 100, 'filled', 140),
  makeTrade('LINK', 'sell', 17.8, 100, 'filled', 135, round(rand(50, 200), 2)),
  makeTrade('DOGE', 'buy', 0.148, 3000, 'filled', 150),
  makeTrade('DOGE', 'sell', 0.159, 3000, 'filled', 145, round(rand(15, 60), 2)),
  makeTrade('ARB', 'buy', 1.02, 600, 'filled', 160),
  makeTrade('ARB', 'sell', 1.14, 600, 'filled', 155, round(rand(30, 100), 2)),
  makeTrade('INJ', 'buy', 24.8, 18, 'filled', 170),
  makeTrade('INJ', 'sell', 27.5, 18, 'filled', 165, round(rand(30, 120), 2)),
  makeTrade('RNDR', 'buy', 8.9, 60, 'filled', 180),
  makeTrade('WIF', 'buy', 1.95, 250, 'filled', 190),
  makeTrade('WIF', 'sell', 2.28, 250, 'partial', 185, round(rand(20, 80), 2)),
  makeTrade('BTC', 'buy', 63500, 0.06, 'filled', 200),
];

export function getTrades(limit = 50): Trade[] {
  return _trades.slice(0, limit).map((t) => ({ ...t }));
}

export function placeOrder(input: {
  symbol: string;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  quantity: number;
  price?: number;
}): Trade {
  const tok = BY_SYMBOL.get(input.symbol.toUpperCase());
  const price = input.price ?? (tok ? tok.price * round(rand(0.998, 1.002), 4) : 1);
  const trade: Trade = {
    trade_id: uid('trd'),
    symbol: input.symbol.toUpperCase(),
    side: input.side,
    order_type: input.order_type,
    price: round(price, 4),
    quantity: input.quantity,
    cost_usd: round(price * input.quantity, 2),
    fee_usd: round(price * input.quantity * 0.001, 4),
    pnl_usd: null,
    status: 'filled',
    mode: _status.mode,
    executed_at: isoNow(),
  };
  _trades.unshift(trade);
  return trade;
}

// ── Portfolio ─────────────────────────────────────────────────────────────────

// PnL-24h % lives here as an evolving baseline (a slow random walk) instead of
// being re-rolled on every read, so the headline number stops teleporting.
let _pnl24hPct = round(rand(-1.5, 3.5), 2);

/**
 * Which of the three Kraken-balance readings the mock serves.
 *
 * `unavailable` is the state production is actually in (no exchange keys in
 * `.env`), so it has to be reachable without a backend — otherwise the only
 * rendering ever exercised in dev is the one that never happens in prod.
 */
export type MockBalanceState = 'fresh' | 'stale' | 'unavailable';

export function asMockBalanceState(v: string | null | undefined): MockBalanceState {
  return v === 'stale' || v === 'unavailable' ? v : 'fresh';
}

// The exchange balance is a *separate measurement*, not a function of the
// simulated book — deriving it from cash (`cash * 0.8`) was the fiction this
// whole change exists to remove, and mirroring it here would keep the mock
// lying in the same shape as the old backend. Own random walk.
let _krakenEquity = round(rand(11_000, 14_000), 2);

interface BalanceSnapshot {
  kraken_balance_usd: number | null;
  balance_source: BalanceSource;
  balance_fetched_at: string | null;
  balance_stale: boolean;
}

function krakenSnapshot(state: MockBalanceState): BalanceSnapshot {
  if (state === 'unavailable') {
    // Exactly what the backend serves with no snapshot row: no number at all.
    return {
      kraken_balance_usd: null,
      balance_source: 'unavailable',
      balance_fetched_at: null,
      balance_stale: false,
    };
  }
  _krakenEquity = round(Math.max(500, _krakenEquity + rand(-40, 45)), 2);
  const ageMs = state === 'stale' ? 8 * 60_000 : rand(8_000, 40_000);
  return {
    kraken_balance_usd: _krakenEquity,
    balance_source: 'kraken_spot',
    balance_fetched_at: isoNow(-ageMs),
    balance_stale: state === 'stale',
  };
}

export function getPortfolio(balanceState: MockBalanceState = 'fresh'): Portfolio {
  // Total value is driven by the server-side simulation's evolving value, so the
  // ticker and the Capital page agree and move together (random walk, not
  // re-randomized each call).
  const total = round(currentPortfolioValue(), 2);
  const totalInvested = round(_positions.reduce((s, p) => s + p.value_usd, 0), 2);
  const cash = round(Math.max(0, total - totalInvested), 2);
  const totalPnl = round(_positions.reduce((s, p) => s + p.unrealized_pnl_usd, 0), 2);
  const pnlPct = total > 0 ? round((totalPnl / total) * 100, 2) : 0;
  const realized24h = _trades
    .filter((t) => new Date(t.executed_at) > new Date(Date.now() - 86400_000) && t.pnl_usd)
    .reduce((s, t) => s + (t.pnl_usd ?? 0), 0);
  _pnl24hPct = round(Math.max(-8, Math.min(9, _pnl24hPct + rand(-0.15, 0.15))), 2);
  return {
    total_value_usd: total,
    cash_usd: cash,
    ...krakenSnapshot(balanceState),
    invested_usd: totalInvested,
    unrealized_pnl_usd: totalPnl,
    unrealized_pnl_pct: pnlPct,
    realized_pnl_24h_usd: round(realized24h, 2),
    pnl_24h_pct: _pnl24hPct,
    updated_at: isoNow(),
  };
}

// ── Portfolio history ─────────────────────────────────────────────────────────

function rangeToPoints(range: string): number {
  switch (range) {
    case '1d': return 24;
    case '7d': return 7 * 4;
    case '30d': return 30;
    case '90d': return 90;
    default: return 30;
  }
}

function rangeToMs(range: string): number {
  switch (range) {
    case '1d': return 3600_000;
    case '7d': return 6 * 3600_000;
    case '30d': return 86400_000;
    case '90d': return 86400_000;
    default: return 86400_000;
  }
}

// Cache the generated series per range so a refetch returns the SAME history
// (instead of a brand-new random walk every 60s). Built backwards from the
// current portfolio value so the chart's right edge meets the live total.
const _historyCache = new Map<string, PricePoint[]>();

export function getPortfolioHistory(range = '30d'): PricePoint[] {
  const cached = _historyCache.get(range);
  if (cached) return cached;
  const points = rangeToPoints(range);
  const stepMs = rangeToMs(range);
  const result: PricePoint[] = [];
  let v = currentPortfolioValue();
  for (let i = 0; i <= points; i++) {
    result.unshift({ t: isoNow(-i * stepMs), price: round(v, 2) });
    v = v / (1 + rand(-0.018, 0.025));
  }
  _historyCache.set(range, result);
  return result;
}

// ── Market tokens ─────────────────────────────────────────────────────────────

let _tokens: MarketToken[] = UNIVERSE.map((tok) => ({
  symbol: tok.symbol,
  coin_id: tok.coin_id,
  name: tok.name,
  price_usd: round(tok.price * round(rand(0.99, 1.01), 4), 4),
  price_change_pct_24h: round(rand(-8, 12), 2),
  volume_24h_usd: round(rand(1e7, 2e9), 0),
  liquidity_usd: round(rand(5e6, 5e8), 0),
  market_cap_usd: round(tok.price * round(rand(8e5, 2e7), 0), 0),
  sentiment_score: round(rand(0.3, 0.95), 2),
  opportunity_score: round(rand(0.2, 0.92), 2),
  is_trending: Math.random() > 0.65,
  updated_at: isoNow(),
}));

export function getTokens(): MarketToken[] {
  return _tokens.map((t) => ({ ...t, price_usd: round(currentPrice(t.symbol), t.price_usd < 10 ? 4 : 2) }));
}

export function getToken(symbol: string): MarketToken | undefined {
  return _tokens.find((t) => t.symbol === symbol.toUpperCase());
}

// ── Price history ─────────────────────────────────────────────────────────────

export function getPriceHistory(symbol: string, range = '1d'): PricePoint[] {
  const tok = BY_SYMBOL.get(symbol.toUpperCase());
  const basePrice = tok ? tok.price : 1;
  const points = rangeToPoints(range);
  const stepMs = rangeToMs(range);
  const result: PricePoint[] = [];
  let p = round(basePrice * rand(0.92, 1.0), 4);
  for (let i = points; i >= 0; i--) {
    p = round(p * (1 + rand(-0.015, 0.02)), 4);
    result.push({
      t: isoNow(-i * stepMs),
      price: p,
      volume: round(rand(1e5, 5e7), 0),
    });
  }
  return result;
}

// ── Opportunities ─────────────────────────────────────────────────────────────

function makeOpportunity(
  symbol: string,
  direction: 'long' | 'short',
  score: number,
  status: Opportunity['status'] = 'pending',
): Opportunity {
  const tok = BY_SYMBOL.get(symbol)!;
  const entry = round(tok.price * rand(0.99, 1.01), 4);
  const sl = round(direction === 'long' ? entry * 0.95 : entry * 1.05, 4);
  const tp = round(direction === 'long' ? entry * 1.15 : entry * 0.85, 4);
  const rr = round(Math.abs(tp - entry) / Math.abs(entry - sl), 2);
  return {
    opportunity_id: uid('opp'),
    symbol,
    direction,
    opportunity_score: round(score, 2),
    confidence: round(rand(0.6, 0.95), 2),
    entry_price: entry,
    stop_loss: sl,
    take_profit: tp,
    position_size_pct: round(rand(2, 8), 1),
    risk_reward_ratio: rr,
    rationale: reason(),
    key_risks: [
      pick(['Volatilité macro élevée', 'Liquidité DEX insuffisante', 'Corrélation BTC forte']),
      pick(['SL serré sur résistance', 'Volume insuffisant', 'Risque de liquidation partielle']),
    ],
    ai_validated: Math.random() > 0.3,
    source: pick(['haiku_triage', 'sonnet_decision', 'manual']),
    status,
    created_at: hoursAgo(round(rand(0.5, 6), 1)),
  };
}

let _opportunities: Opportunity[] = [
  makeOpportunity('BTC', 'long', 0.88),
  makeOpportunity('ETH', 'long', 0.82),
  makeOpportunity('SOL', 'long', 0.77),
  makeOpportunity('INJ', 'short', 0.71),
  makeOpportunity('WIF', 'long', 0.65),
  makeOpportunity('RNDR', 'long', 0.61),
];

export function getOpportunities(status: 'pending' | 'all' = 'pending'): Opportunity[] {
  if (status === 'all') return _opportunities.map((o) => ({ ...o }));
  return _opportunities.filter((o) => o.status === 'pending').map((o) => ({ ...o }));
}

export function approveOpportunity(id: string): Opportunity | null {
  const idx = _opportunities.findIndex((o) => o.opportunity_id === id);
  if (idx === -1) return null;
  _opportunities[idx] = { ..._opportunities[idx], status: 'approved' };
  return { ..._opportunities[idx] };
}

export function rejectOpportunity(id: string, _reason?: string): Opportunity | null {
  const idx = _opportunities.findIndex((o) => o.opportunity_id === id);
  if (idx === -1) return null;
  _opportunities[idx] = { ..._opportunities[idx], status: 'rejected' };
  return { ..._opportunities[idx] };
}

// ── News ──────────────────────────────────────────────────────────────────────

const NEWS_TITLES = [
  'Bitcoin atteint un nouveau sommet mensuel à {p}$',
  'Ethereum : mise à jour du réseau prévue pour {d}',
  'Solana dépasse les {p} transactions par seconde',
  'La SEC approuve un nouveau ETF crypto aux États-Unis',
  'Le marché DeFi dépasse 80 milliards de TVL',
  'Cardano intègre une nouvelle solution de scaling',
  'Avalanche signe un partenariat avec une grande banque',
  'Les whales accumulent du BTC selon les données on-chain',
  'Analyse : l\'altseason pourrait débuter sous 30 jours',
  'Chainlink déploie son oracle sur 5 nouvelles chaînes',
  'Injective lance un DEX cross-chain haute performance',
  'Render Network intègre de nouveaux GPU providers',
  'Le volume DEX dépasse les échanges centralisés ce mois-ci',
  'FTX : nouvelles révélations lors du procès en cours',
  'Dogecoin : Elon Musk mentionne encore la crypto',
  'Arbitrum propose un nouveau vote de gouvernance',
  'BTC dominance atteint 55%, signalant une rotation',
  'Régulation crypto en Europe : MiCA entre en vigueur',
  'DogWifHat (WIF) : pump de 40% en 24h sur Solana',
  'Les mineurs Bitcoin vendent massivement après le halving',
];

const SOURCES = ['CoinDesk', 'CoinTelegraph', 'The Block', 'Decrypt', 'Bloomberg Crypto', 'Reuters'];

let _news: NewsItem[] = NEWS_TITLES.map((title, i) => {
  const sym = pick(UNIVERSE).symbol;
  return {
    id: uid('news'),
    title: title
      .replace('{p}', String(Math.floor(rand(10000, 75000))))
      .replace('{d}', '15 août 2025'),
    url: `https://example.com/news/${i + 1}`,
    source: pick(SOURCES),
    symbols: [sym, ...(Math.random() > 0.6 ? [pick(UNIVERSE).symbol] : [])],
    sentiment: round(rand(-0.4, 0.9), 2),
    published_at: hoursAgo(round(rand(0.5, 72), 1)),
  };
}).sort((a, b) => new Date(b.published_at).getTime() - new Date(a.published_at).getTime());

export function getNews(limit = 20): NewsItem[] {
  return _news.slice(0, limit);
}

// ── Worker decisions ──────────────────────────────────────────────────────────

const DECISIONS = [
  'LONG_SIGNAL',
  'SHORT_SIGNAL',
  'HOLD',
  'ESCALATE',
  'NO_OPPORTUNITY',
];

const JUSTIFICATIONS = [
  'Momentum technique confirmé par indicateurs RSI/MACD ; volume on-chain en hausse significative.',
  'Sentiment social négatif et flux vendeurs institutionnels suggèrent une correction imminente.',
  'Absence de catalyseur clair ; attendre une consolidation avant d\'entrer en position.',
  'Score d\'opportunité élevé mais incertitude macro persistante ; escalade pour validation senior.',
  'Corrélation BTC > 0.9 sur 7 jours ; pas de divergence exploitable détectée.',
  'Breakout de résistance majeure confirmé avec volume 2x la moyenne 30j.',
  'Pattern double bottom validé ; risque/rendement favorable au-dessus de 1:3.',
  'Liquidation de positions short en cascade attendue si prix franchit résistance.',
];

let _decisions: WorkerDecision[] = UNIVERSE.flatMap((tok) => [
  {
    id: uid('dec'),
    symbol: tok.symbol,
    worker: 'haiku' as const,
    decision: pick(DECISIONS),
    opportunity_score: round(rand(0.1, 0.95), 2),
    confidence: round(rand(0.5, 0.9), 2),
    justification: pick(JUSTIFICATIONS),
    escalated: Math.random() > 0.7,
    created_at: hoursAgo(round(rand(0.5, 8), 1)),
  },
  {
    id: uid('dec'),
    symbol: tok.symbol,
    worker: 'sonnet' as const,
    decision: pick(DECISIONS),
    opportunity_score: round(rand(0.4, 0.95), 2),
    confidence: round(rand(0.6, 0.95), 2),
    justification: pick(JUSTIFICATIONS),
    escalated: false,
    created_at: hoursAgo(round(rand(0.1, 3), 1)),
  },
]).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

export function getDecisions(limit = 30): WorkerDecision[] {
  return _decisions.slice(0, limit);
}

// ── Signals ───────────────────────────────────────────────────────────────────

type Signal = PriceEvent | AnalysisEvent | SentimentEvent | DecisionEvent;

function makeSignals(): Signal[] {
  const signals: Signal[] = [];
  for (const tok of UNIVERSE) {
    signals.push({
      event_type: 'PriceEvent',
      source: 'coingecko',
      symbol: tok.symbol,
      coin_id: tok.coin_id,
      price_usd: String(round(tok.price * rand(0.99, 1.01), 4)),
      price_change_pct_24h: round(rand(-8, 12), 2),
      volume_24h_usd: String(round(rand(1e7, 1e9), 0)),
      is_trending: Math.random() > 0.6,
      occurred_at: hoursAgo(round(rand(0, 1), 2)),
    } satisfies PriceEvent);

    signals.push({
      event_type: 'AnalysisEvent',
      source: 'haiku_worker',
      symbol: tok.symbol,
      opportunity_score: round(rand(0.1, 0.95), 2),
      confidence: round(rand(0.5, 0.9), 2),
      reason: reason(),
      escalate: Math.random() > 0.7,
      occurred_at: hoursAgo(round(rand(0, 2), 2)),
    } satisfies AnalysisEvent);

    signals.push({
      event_type: 'SentimentEvent',
      source: 'sentiment_worker',
      symbol: tok.symbol,
      sentiment_score: round(rand(-0.5, 0.95), 2),
      confidence: round(rand(0.5, 0.9), 2),
      model_name: 'haiku',
      input_kind: pick(['social', 'news']),
      sample_size: Math.floor(rand(20, 500)),
      occurred_at: hoursAgo(round(rand(0, 3), 2)),
    } satisfies SentimentEvent);

    if (Math.random() > 0.4) {
      signals.push({
        event_type: 'DecisionEvent',
        source: 'sonnet_worker',
        symbol: tok.symbol,
        direction: Math.random() > 0.5 ? 'long' : 'short',
        opportunity_score: round(rand(0.4, 0.95), 2),
        confidence: round(rand(0.6, 0.95), 2),
        rationale: reason(),
        key_risks: [pick(['Volatilité élevée', 'Liquidité faible', 'Corrélation macro'])],
        ai_validated: Math.random() > 0.3,
        occurred_at: hoursAgo(round(rand(0, 1), 2)),
      } satisfies DecisionEvent);
    }
  }
  return signals.sort(
    (a, b) =>
      new Date(b.occurred_at ?? 0).getTime() - new Date(a.occurred_at ?? 0).getTime(),
  );
}

let _signals: Signal[] = makeSignals();

export function getSignals(limit = 30): Signal[] {
  return _signals.slice(0, limit);
}

// ── Risk exposure ─────────────────────────────────────────────────────────────

export function getRiskExposure(): RiskExposure {
  const positions = getPositions();
  const portfolioVal = getPortfolio().total_value_usd;
  const byAsset: AssetExposure[] = positions.map((p) => ({
    symbol: p.symbol,
    exposure_usd: p.value_usd,
    exposure_pct: round((p.value_usd / (portfolioVal || 1)) * 100, 2),
    limit_pct: 30,
    protected: p.protected,
  }));

  const total = byAsset.reduce((s, a) => s + a.exposure_usd, 0);

  return {
    total_exposure_usd: round(total, 2),
    total_exposure_pct: portfolioVal > 0 ? round((total / portfolioVal) * 100, 2) : 0,
    max_exposure_pct: 80,
    by_asset: byAsset,
    protected_positions: _positions.filter((p) => p.protected).length,
    open_positions: _positions.length,
    daily_loss_usd: round(rand(0, 800), 2),
    daily_loss_limit_usd: 2000,
    updated_at: isoNow(),
  };
}

// ── Risk limits ───────────────────────────────────────────────────────────────

export function getRiskLimits(): RiskLimit[] {
  const portfolioVal = getPortfolio().total_value_usd;
  const exposure = getRiskExposure();
  return [
    {
      key: 'max_portfolio_exposure',
      label: 'Exposition maximale portefeuille',
      value: round(exposure.total_exposure_pct, 1),
      max: 80,
      unit: '%',
      breached: exposure.total_exposure_pct > 80,
    },
    {
      key: 'max_single_asset',
      label: 'Exposition maximale par actif',
      value: round(Math.max(...(exposure.by_asset.map((a) => a.exposure_pct)), 0), 1),
      max: 30,
      unit: '%',
      breached: exposure.by_asset.some((a) => a.exposure_pct > 30),
    },
    {
      key: 'daily_loss_limit',
      label: 'Perte journalière maximale',
      value: round(exposure.daily_loss_usd, 0),
      max: 2000,
      unit: 'USD',
      breached: exposure.daily_loss_usd > 2000,
    },
    {
      key: 'max_open_positions',
      label: 'Positions ouvertes maximum',
      value: _positions.length,
      max: 10,
      unit: 'positions',
      breached: _positions.length > 10,
    },
    {
      key: 'min_cash_reserve',
      label: 'Réserve de liquidité minimum',
      value: round((getPortfolio().cash_usd / portfolioVal) * 100, 1),
      max: 20,
      unit: '%',
      breached: (getPortfolio().cash_usd / portfolioVal) * 100 < 20,
    },
  ];
}

// ── Risk alerts ───────────────────────────────────────────────────────────────

let _riskAlerts: RiskAlert[] = [
  {
    id: uid('alrt'),
    level: 'warning',
    symbol: 'SOL',
    message: RISK_MESSAGES[4].replace('{s}', 'SOL'),
    created_at: hoursAgo(1.5),
  },
  {
    id: uid('alrt'),
    level: 'info',
    symbol: 'BTC',
    message: RISK_MESSAGES[0].replace('{s}', 'BTC'),
    created_at: hoursAgo(2),
  },
  {
    id: uid('alrt'),
    level: 'warning',
    message: RISK_MESSAGES[3],
    created_at: hoursAgo(0.5),
  },
  {
    id: uid('alrt'),
    level: 'critical',
    symbol: 'AVAX',
    message: RISK_MESSAGES[1].replace('{s}', 'AVAX'),
    created_at: hoursAgo(0.25),
  },
  {
    id: uid('alrt'),
    level: 'info',
    message: RISK_MESSAGES[2],
    created_at: daysAgo(1),
  },
];

export function getRiskAlerts(limit = 30): RiskAlert[] {
  return _riskAlerts.slice(0, limit);
}
