/**
 * Server-side stateful simulation — the single source of truth for the mock
 * backend. Unlike the old client-side `MockEventSource` (which regenerated
 * random data per browser and lost everything on refresh), this lives in the
 * Next.js server process as a module singleton: state evolves by a *random
 * walk* over wall-clock time, a rolling event log persists across requests and
 * refreshes, and correlated decision chains are stored so a clicked event
 * yields its REAL lineage.
 *
 * Everything is ticked lazily: each request calls `tick()`, which advances the
 * world by however much real time has elapsed (capped, to avoid bursts after an
 * idle period). No background timers — deterministic and serverless-friendly.
 */
import { BY_SYMBOL, pick, rand, reason, round, uid, UNIVERSE } from './universe';
import type {
  AnalysisEvent,
  CmiEvent,
  DecisionEvent,
  DerivativesEvent,
  DeveloperEvent,
  FundamentalsEvent,
  OrderExecutedEvent,
  PriceEvent,
  RiskApprovedEvent,
  SentimentEvent,
  WsMessage,
} from '@/lib/types/events';
import type { DecisionTrace } from '@/lib/types/content';

interface LoggedEvent extends WsMessage {
  seq: number;
}

const MAX_LOG = 600;

// ── evolving world state ────────────────────────────────────────────────────
const prices = new Map<string, number>(UNIVERSE.map((t) => [t.symbol, t.price]));
let seq = 0;
let log: LoggedEvent[] = [];
const chains = new Map<string, DecisionTrace>();
let portfolioValue = 128_450;
let lastTick = 0; // set on first tick
let seeded = false;

// scheduler accumulators (ms) + target intervals per stream
const RATE: Record<string, number> = {
  price: 1400,
  sentiment: 6500,
  analysis: 4200,
  decision: 8000,
  chain: 13_000,
  portfolio: 5000,
  // Slow cadence: the real collectors republish these on hours-long cycles
  // (funding every 8h, TVL/dev activity daily), not on the tick pace of
  // price/sentiment. ~1 event per 15 price ticks keeps the feed showing them
  // without drowning the faster streams.
  derivatives: 21_000,
  fundamentals: 21_000,
  developer: 21_000,
};
const sched: Record<string, number> = {
  price: 0,
  sentiment: 0,
  analysis: 0,
  decision: 0,
  chain: 0,
  portfolio: 0,
  derivatives: 0,
  fundamentals: 0,
  developer: 0,
};

// ── helpers ─────────────────────────────────────────────────────────────────
function drift(sym: string, pct = 0.004): number {
  const cur = prices.get(sym) ?? 1;
  const next = round(cur * (1 + rand(-pct, pct)), 4);
  prices.set(sym, next);
  return next;
}

function base<T extends CmiEvent['event_type']>(type: T, source: string, symbol: string, atMs: number, corr?: string) {
  return {
    event_id: uid('evt'),
    event_type: type,
    schema_version: 1,
    occurred_at: new Date(atMs).toISOString(),
    source,
    correlation_id: corr ?? uid('corr'),
    symbol,
  };
}

function push(event: CmiEvent, topic: string, atMs: number) {
  seq += 1;
  log.push({ seq, topic, event, ts: new Date(atMs).toISOString() });
  if (log.length > MAX_LOG) log = log.slice(-MAX_LOG);
}

// ── event factories ──────────────────────────────────────────────────────────
function emitPrice(atMs: number) {
  const t = pick(UNIVERSE);
  const price = drift(t.symbol);
  const e: PriceEvent = {
    ...base('PriceEvent', 'coingecko', t.symbol, atMs),
    coin_id: t.coin_id,
    price_usd: String(price),
    volume_24h_usd: String(Math.round(rand(2e7, 4e9))),
    price_change_pct_24h: round(rand(-9, 12), 2),
    is_trending: Math.random() > 0.7,
  };
  push(e, 'market.price.events', atMs);
}

function emitSentiment(atMs: number) {
  const t = pick(UNIVERSE);
  const e: SentimentEvent = {
    ...base('SentimentEvent', 'sentiment-service', t.symbol, atMs),
    sentiment_score: round(rand(-0.8, 0.9), 2),
    confidence: round(rand(0.6, 0.98), 2),
    model_name: 'ElKulako/cryptobert',
    input_kind: pick(['social', 'news']),
    sample_size: Math.floor(rand(10, 120)),
  };
  push(e, 'market.sentiment.events', atMs);
}

function emitAnalysis(atMs: number) {
  const t = pick(UNIVERSE);
  const score = Math.floor(rand(45, 95));
  const e: AnalysisEvent = {
    ...base('AnalysisEvent', 'ai-worker-haiku', t.symbol, atMs),
    opportunity_score: score,
    confidence: round(rand(0.55, 0.95), 2),
    reason: reason(),
    price_change_pct_24h: round(rand(-6, 14), 1),
    volume_spike_ratio: round(rand(1, 5), 1),
    sentiment_score: round(rand(-0.5, 0.8), 2),
    social_growth: round(rand(0, 1.8), 2),
    escalate: score >= 78,
  };
  push(e, 'market.analysis.events', atMs);
}

function emitDecision(atMs: number) {
  const t = pick(UNIVERSE);
  const e: DecisionEvent = {
    ...base('DecisionEvent', 'ai-worker-sonnet', t.symbol, atMs),
    direction: Math.random() > 0.25 ? 'long' : 'short',
    opportunity_score: Math.floor(rand(60, 92)),
    confidence: round(rand(0.6, 0.93), 2),
    rationale:
      'Momentum soutenu, liquidité saine et sentiment corroborant. Entrée justifiée par le ratio risque/rendement.',
    key_risks: pick([
      ['volatilité récente', 'concentration sur un seul DEX'],
      ['news non confirmée', 'faible profondeur de carnet'],
      ['corrélation BTC élevée'],
    ]),
    ai_validated: Math.random() > 0.3,
  };
  push(e, 'decision.events', atMs);
}

function emitDerivatives(atMs: number) {
  const t = pick(UNIVERSE);
  const funding = round(rand(-0.0003, 0.0003), 6);
  const e: DerivativesEvent = {
    ...base('DerivativesEvent', 'binance-futures', t.symbol, atMs),
    funding_rate_8h: funding,
    funding_annualized_pct: round(funding * 3 * 365 * 100, 2),
    open_interest_usd: String(Math.round(rand(5e6, 8e8))),
    open_interest_change_pct_24h: Math.random() > 0.5 ? round(rand(-15, 15), 2) : null,
    long_short_account_ratio: round(rand(0.6, 1.8), 2),
  };
  push(e, 'market.derivatives.events', atMs);
}

function emitFundamentals(atMs: number) {
  const t = pick(UNIVERSE);
  const hasFees = Math.random() > 0.5;
  const e: FundamentalsEvent = {
    ...base('FundamentalsEvent', 'defillama', t.symbol, atMs),
    coin_id: t.coin_id,
    tvl_usd: String(Math.round(rand(2e6, 4e9))),
    tvl_change_pct_7d: round(rand(-12, 18), 2),
    fees_24h_usd: hasFees ? String(Math.round(rand(1e4, 3e6))) : null,
    fees_change_pct_7d: hasFees ? round(rand(-20, 25), 2) : null,
    next_unlock_at: Math.random() > 0.6 ? new Date(atMs + rand(1, 30) * 86_400_000).toISOString() : null,
    next_unlock_pct_supply: Math.random() > 0.6 ? round(rand(0.1, 4), 2) : null,
    has_unlock_schedule: Math.random() > 0.4,
  };
  push(e, 'market.fundamentals.events', atMs);
}

function emitDeveloper(atMs: number) {
  const t = pick(UNIVERSE);
  const repoCount = Math.floor(rand(1, 40));
  const e: DeveloperEvent = {
    ...base('DeveloperEvent', 'github', t.symbol, atMs),
    coin_id: t.coin_id,
    repo_count: repoCount,
    commit_ratio_4w: round(rand(0.2, 2.2), 2),
    pr_ratio_4w: round(rand(0.1, 2), 2),
    days_since_push: Math.floor(rand(0, 90)),
    star_growth_pct_7d: round(rand(-3, 8), 2),
    all_repos_archived: false,
  };
  push(e, 'market.developer.events', atMs);
}

/**
 * Drifts the mock portfolio value the Capital page reads. It used to also push
 * a `PortfolioChangedEvent` on the socket; no backend service publishes that
 * event and no component subscribes to it, so the only thing the push did was
 * put a row in the feed that production can never show.
 */
function emitPortfolio(_atMs: number) {
  portfolioValue = round(portfolioValue * (1 + rand(-0.004, 0.005)), 2);
}

/**
 * Emit a full correlated chain (price→analysis→decision→risk→order) sharing one
 * correlation_id, and STORE its real lineage so `getTrace(cid)` returns it.
 * Events are stamped a few seconds apart ending at `atMs`.
 */
function emitChain(atMs: number) {
  const t = pick(UNIVERSE);
  const corr = uid('corr');
  const price = drift(t.symbol, 0.002);
  const t0 = atMs - 42_000;
  const score = Math.floor(rand(78, 94));
  const conf = round(rand(0.72, 0.93), 2);
  const size = round(rand(2, 6), 1);
  const sl = round(price * 0.95, 2);
  const tp = round(price * 1.11, 2);
  const rr = round(rand(1.8, 2.8), 2);
  const sent = round(rand(0.2, 0.8), 2);

  const priceEv: PriceEvent = {
    ...base('PriceEvent', 'coingecko', t.symbol, t0, corr),
    coin_id: t.coin_id,
    price_usd: String(price),
    volume_24h_usd: String(Math.round(rand(2e7, 4e9))),
    price_change_pct_24h: round(rand(2, 10), 2),
    is_trending: true,
  };
  const analysisEv: AnalysisEvent = {
    ...base('AnalysisEvent', 'ai-worker-haiku', t.symbol, atMs - 30_000, corr),
    opportunity_score: score,
    confidence: round(rand(0.7, 0.95), 2),
    reason: reason(),
    price_change_pct_24h: round(rand(2, 12), 1),
    volume_spike_ratio: round(rand(1.8, 4), 1),
    sentiment_score: sent,
    social_growth: round(rand(0.3, 1.6), 2),
    escalate: true,
  };
  const decisionEv: DecisionEvent = {
    ...base('DecisionEvent', 'ai-worker-sonnet', t.symbol, atMs - 22_000, corr),
    direction: 'long',
    opportunity_score: Math.floor(rand(80, 92)),
    confidence: conf,
    rationale: 'Momentum soutenu, liquidité saine et sentiment corroborant. Entrée justifiée par le R/R.',
    key_risks: ['volatilité macro', 'corrélation BTC élevée'],
    ai_validated: true,
  };
  const riskEv: RiskApprovedEvent = {
    ...base('RiskApprovedEvent', 'risk-engine', t.symbol, atMs - 12_000, corr),
    direction: 'long',
    entry_price: round(price, 2),
    stop_loss: sl,
    take_profit: tp,
    confidence: conf,
    position_size_pct: round(size / 100, 3),
    risk_reward_ratio: rr,
  };
  const orderEv: OrderExecutedEvent = {
    ...base('OrderExecutedEvent', 'trading-engine', t.symbol, atMs, corr),
    order_id: uid('ord'),
    side: 'buy',
    order_type: 'market',
    price: round(price, 2),
    volume: round(rand(0.1, 3), 3),
    cost: round(rand(500, 6000), 2),
    fee: round(rand(1, 12), 2),
    status: 'filled',
    mode: 'dry_run',
  };

  push(priceEv, 'market.price.events', t0);
  push(analysisEv, 'market.analysis.events', atMs - 30_000);
  push(decisionEv, 'decision.events', atMs - 22_000);
  push(riskEv, 'risk.approved.events', atMs - 12_000);
  push(orderEv, 'trading.orders.events', atMs);

  chains.set(corr, {
    correlation_id: corr,
    symbol: t.symbol,
    stages: [
      { kind: 'price', at: priceEv.occurred_at!, reached: true, summary: `Tick prix ${t.symbol} $${round(price, 2)}`,
        detail: { source: 'coingecko', price_usd: round(price, 2), change_24h_pct: priceEv.price_change_pct_24h } },
      { kind: 'sentiment', at: analysisEv.occurred_at!, reached: true, summary: 'Sentiment social/news agrégé',
        detail: { score: sent, model: 'ElKulako/cryptobert', sample: analysisEv.volume_spike_ratio ?? 0 } },
      { kind: 'analysis', at: analysisEv.occurred_at!, reached: true, summary: 'Haiku — triage (escalade)',
        detail: { opportunity_score: score, confidence: analysisEv.confidence, escalate: true } },
      { kind: 'decision', at: decisionEv.occurred_at!, reached: true, summary: 'Sonnet — LONG validé',
        detail: { direction: 'long', confidence: conf, ai_validated: true } },
      { kind: 'risk', at: riskEv.occurred_at!, reached: true, summary: 'Risque — sizing & protection',
        detail: { size_pct: size, stop_loss: sl, take_profit: tp, rr } },
      { kind: 'order', at: orderEv.occurred_at!, reached: true, summary: 'Ordre exécuté (paper)',
        detail: { status: 'filled', price: round(price, 2), fee: orderEv.fee ?? 0, mode: 'dry_run' } },
    ],
  });
}

const EMIT: Record<string, (atMs: number) => void> = {
  price: emitPrice,
  sentiment: emitSentiment,
  analysis: emitAnalysis,
  decision: emitDecision,
  portfolio: emitPortfolio,
  chain: emitChain,
  derivatives: emitDerivatives,
  fundamentals: emitFundamentals,
  developer: emitDeveloper,
};

// ── backfill: instant history on first load ──────────────────────────────────
function seed() {
  if (seeded) return;
  seeded = true;
  const now = Date.now();
  lastTick = now;
  // generate ~4 minutes of history so the feed is populated immediately
  for (let ms = now - 240_000; ms < now; ms += 1500) {
    emitPrice(ms);
    if (ms % 6000 < 1500) emitSentiment(ms);
    if (ms % 4500 < 1500) emitAnalysis(ms);
    if (ms % 8000 < 1500) emitDecision(ms);
    if (ms % 5000 < 1500) emitPortfolio(ms);
    if (ms % 21_000 < 1500) emitDerivatives(ms);
    if (ms % 21_000 < 1500) emitFundamentals(ms);
    if (ms % 21_000 < 1500) emitDeveloper(ms);
  }
  // a few complete chains so trace-able events already exist
  emitChain(now - 90_000);
  emitChain(now - 40_000);
  emitChain(now - 8_000);
}

// ── the lazy clock ────────────────────────────────────────────────────────────
export function tick() {
  seed();
  const now = Date.now();
  let dt = Math.min(now - lastTick, 20_000); // cap catch-up
  lastTick = now;
  for (const key of Object.keys(RATE)) {
    sched[key] += dt;
    let guard = 0;
    while (sched[key] >= RATE[key] && guard < 15) {
      sched[key] -= RATE[key];
      guard += 1;
      EMIT[key](now);
    }
  }
}

// ── public getters ────────────────────────────────────────────────────────────
export interface RecentEvents {
  cursor: number;
  events: { seq: number; topic: string; event: CmiEvent; ts: string }[];
}

export function recentEvents(sinceSeq: number, limit = 150): RecentEvents {
  tick();
  const items = log.filter((e) => e.seq > sinceSeq).slice(-limit);
  return { cursor: seq, events: items };
}

export function getStoredTrace(cid: string): DecisionTrace | null {
  tick();
  return chains.get(cid) ?? null;
}

/** Current evolving price for a symbol (drives coherent portfolio/market reads). */
export function currentPrice(symbol: string): number {
  return prices.get(symbol.toUpperCase()) ?? BY_SYMBOL.get(symbol.toUpperCase())?.price ?? 1;
}

export function currentPortfolioValue(): number {
  tick();
  return portfolioValue;
}
