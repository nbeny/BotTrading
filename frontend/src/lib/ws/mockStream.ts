import type {
  AnalysisEvent,
  CmiEvent,
  DecisionEvent,
  OrderExecutedEvent,
  PortfolioChangedEvent,
  PositionChangedEvent,
  PriceEvent,
  RiskApprovedEvent,
  SentimentEvent,
} from '@/lib/types/events';
import { BY_SYMBOL, pick, rand, reason, round, uid, UNIVERSE } from '@/lib/mock/universe';

type Emit = (event: CmiEvent, topic: string) => void;

/**
 * Synthetic Kafka-shaped event generator used in standalone/demo mode. Emits a
 * realistic mix of price ticks, Haiku analyses, Sonnet decisions, risk
 * approvals and execution/portfolio events so the whole terminal comes alive
 * without a running backend.
 */
export class MockEventSource {
  private timers: ReturnType<typeof setInterval>[] = [];
  private portfolioValue = 128_450;

  constructor(private emit: Emit) {}

  start() {
    this.timers.push(setInterval(() => this.price(), 1400));
    this.timers.push(setInterval(() => this.analysis(), 4200));
    this.timers.push(setInterval(() => this.decision(), 8000));
    this.timers.push(setInterval(() => this.risk(), 11_000));
    this.timers.push(setInterval(() => this.sentiment(), 6500));
    this.timers.push(setInterval(() => this.portfolio(), 5000));
    this.timers.push(setInterval(() => this.order(), 15_000));
    this.timers.push(setInterval(() => this.position(), 9000));
  }

  stop() {
    this.timers.forEach(clearInterval);
    this.timers = [];
  }

  private now() {
    return new Date().toISOString();
  }

  private base<T extends CmiEvent['event_type']>(type: T, source: string, symbol: string) {
    return {
      event_id: uid('evt'),
      event_type: type,
      schema_version: 1,
      occurred_at: this.now(),
      source,
      correlation_id: uid('corr'),
      symbol,
    };
  }

  private price() {
    const t = pick(UNIVERSE);
    const change = round(rand(-9, 12), 2);
    const e: PriceEvent = {
      ...this.base('PriceEvent', 'coingecko', t.symbol),
      coin_id: t.coin_id,
      price_usd: String(round(t.price * (1 + change / 400), 4)),
      volume_24h_usd: String(Math.round(rand(2e7, 4e9))),
      price_change_pct_24h: change,
      is_trending: Math.random() > 0.7,
    };
    this.emit(e, 'market.price.events');
  }

  private sentiment() {
    const t = pick(UNIVERSE);
    const e: SentimentEvent = {
      ...this.base('SentimentEvent', 'sentiment-service', t.symbol),
      sentiment_score: round(rand(-0.8, 0.9), 2),
      confidence: round(rand(0.6, 0.98), 2),
      model_name: 'ElKulako/cryptobert',
      input_kind: pick(['social', 'news']),
      sample_size: Math.floor(rand(10, 120)),
    };
    this.emit(e, 'market.sentiment.events');
  }

  private analysis() {
    const t = pick(UNIVERSE);
    const score = Math.floor(rand(45, 95));
    const e: AnalysisEvent = {
      ...this.base('AnalysisEvent', 'ai-worker-haiku', t.symbol),
      opportunity_score: score,
      confidence: round(rand(0.55, 0.95), 2),
      reason: reason(),
      price_change_pct_24h: round(rand(-6, 14), 1),
      volume_spike_ratio: round(rand(1, 5), 1),
      sentiment_score: round(rand(-0.5, 0.8), 2),
      social_growth: round(rand(0, 1.8), 2),
      escalate: score >= 78,
    };
    this.emit(e, 'market.analysis.events');
  }

  private decision() {
    const t = pick(UNIVERSE);
    const e: DecisionEvent = {
      ...this.base('DecisionEvent', 'ai-worker-sonnet', t.symbol),
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
    this.emit(e, 'decision.events');
  }

  private risk() {
    const t = pick(UNIVERSE);
    const price = t.price;
    const e: RiskApprovedEvent = {
      ...this.base('RiskApprovedEvent', 'risk-engine', t.symbol),
      direction: 'long',
      entry_price: round(price, 2),
      stop_loss: round(price * 0.95, 2),
      take_profit: round(price * 1.11, 2),
      confidence: round(rand(0.7, 0.92), 2),
      position_size_pct: round(rand(0.02, 0.06), 3),
      risk_reward_ratio: round(rand(1.4, 3.2), 2),
    };
    this.emit(e, 'risk.approved.events');
  }

  private order() {
    const t = pick(UNIVERSE);
    const e: OrderExecutedEvent = {
      ...this.base('OrderExecutedEvent', 'trading-engine', t.symbol),
      order_id: uid('ord'),
      side: Math.random() > 0.5 ? 'buy' : 'sell',
      order_type: 'market',
      price: round(t.price, 2),
      volume: round(rand(0.1, 4), 3),
      cost: round(rand(500, 6000), 2),
      fee: round(rand(1, 12), 2),
      status: 'filled',
      mode: 'paper',
    };
    this.emit(e, 'trading.orders.events');
  }

  private position() {
    const t = pick(UNIVERSE);
    const e: PositionChangedEvent = {
      ...this.base('PositionChangedEvent', 'trading-engine', t.symbol),
      position_id: uid('pos'),
      change: pick(['opened', 'updated', 'closed']),
      quantity: round(rand(0.5, 8), 3),
      avg_price: round(t.price, 2),
      unrealized_pnl: round(rand(-800, 1600), 2),
    };
    this.emit(e, 'trading.positions.events');
  }

  private portfolio() {
    this.portfolioValue = round(this.portfolioValue * (1 + rand(-0.004, 0.005)), 2);
    const e: PortfolioChangedEvent = {
      ...this.base('PortfolioChangedEvent', 'portfolio-service', 'PORTFOLIO'),
      total_value_usd: this.portfolioValue,
      cash_usd: round(this.portfolioValue * 0.32, 2),
      pnl_24h_pct: round(rand(-3, 5), 2),
    };
    this.emit(e, 'portfolio.events');
  }
}

export { BY_SYMBOL };
