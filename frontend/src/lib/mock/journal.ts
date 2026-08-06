import type {
  JournalAttribution,
  JournalCalibration,
  JournalDecisionsPage,
  JournalRow,
  JournalWindow,
} from '@/lib/types/journal';
import type { DecisionExplain } from '@/lib/types/explain';

const SYMBOLS = ['BTC', 'ETH', 'SOL', 'DOGE', 'AVAX', 'LINK'];

function row(i: number): JournalRow {
  const score = 35 + ((i * 7) % 60);
  const judged = i % 5 !== 0;
  const pnl = judged ? Math.round(((i % 11) - 5) * 8) / 10 : null;
  return {
    time: new Date(Date.now() - i * 3_600_000).toISOString(),
    event_id: `jr-${i}`,
    symbol: SYMBOLS[i % SYMBOLS.length],
    score,
    confidence: 0.4 + (i % 5) * 0.1,
    escalated: score >= 55,
    sonnet_called: score >= 60,
    sonnet_validated: score >= 60 ? i % 3 !== 0 : null,
    direction: score >= 60 ? (i % 2 ? 'long' : 'short') : null,
    passed: score >= 70,
    risk_verdict: score >= 70 ? (i % 4 === 0 ? 'rejected' : 'approved') : null,
    pnl_pct: pnl,
    outcome: pnl === null ? null : pnl > 0 ? 'take_profit' : 'stop_loss',
    correlation_id: i % 3 === 0 ? `cid-${i}` : null,
  };
}

export function getJournalDecisions(
  window: JournalWindow,
  limit: number,
  offset: number,
): JournalDecisionsPage {
  const total = 120;
  const rows = Array.from({ length: Math.min(limit, total - offset) }, (_, k) => row(offset + k));
  return { window, horizon: '4h', current_threshold: 101, total, rows };
}

export function getJournalCalibration(window: JournalWindow, threshold: number): JournalCalibration {
  const all = Array.from({ length: 120 }, (_, i) => row(i));
  const bucket = (t: number) => {
    const sel = all.filter((r) => r.score !== null && r.score >= t);
    const judged = sel.filter((r) => r.pnl_pct !== null);
    const n = judged.length;
    const suff = n >= 20;
    const wins = judged.filter((r) => (r.pnl_pct ?? 0) > 0).length;
    const totalPnl = judged.reduce((s, r) => s + (r.pnl_pct ?? 0), 0);
    return {
      threshold: t,
      selected: sel.length,
      judged: n,
      sufficient: suff,
      win_rate: suff ? Math.round((wins / n) * 10000) / 10000 : null,
      avg_pnl_pct: suff ? Math.round((totalPnl / n) * 10000) / 10000 : null,
      total_pnl_pct: suff ? Math.round(totalPnl * 10000) / 10000 : null,
    };
  };
  return { window, horizon: '4h', min_n: 20, requested: bucket(threshold), current: bucket(101) };
}

export function getJournalAttribution(window: JournalWindow): JournalAttribution {
  return {
    window,
    horizon: '4h',
    n: 96,
    min_n: 20,
    sufficient: true,
    factors: [
      { key: 'momentum', n: 96, correlation: 0.31 },
      { key: 'volume', n: 96, correlation: 0.12 },
      { key: 'sentiment', n: 88, correlation: -0.04 },
      { key: 'liquidity', n: 14, correlation: null }, // n < min_n → « — »
    ],
  };
}

export function getExplain(id: string): DecisionExplain {
  return {
    id,
    symbol: 'SOL',
    direction: 'long',
    score: {
      value: 64,
      confidence: 0.58,
      axes: { volume_growth: 0.8, market_trend: 0.65, positioning: 0.55, liquidity_score: 0.7, social_score: 0.5, news_score: 0.45 },
      axes_total: 8,
      insufficient_evidence: false,
      computed_at: new Date().toISOString(),
    },
    triage: {
      score: 64,
      confidence: 0.58,
      factors: { momentum: 0.7, volume: 0.6, sentiment: 0.4, liquidity: 0.55 },
      dominant_factor: 'momentum',
      escalated: true,
      sonnet_called: true,
      sonnet_validated: true,
      sonnet_score: 70,
      sonnet_direction: 'long',
      skip_reason: null,
    },
    risk: { verdict: 'rejected', reason: 'score 64 < floor 70' },
    pipeline: {
      reached_stage: 'risk',
      blocked_at: 'risk',
      block_reason: 'score 64 < floor 70',
      escalated: true,
      sonnet_called: true,
      sonnet_validated: true,
      last_event_at: new Date().toISOString(),
    },
    counterfactual: { horizon: '4h', pnl_pct: 2.1, outcome: 'take_profit' },
    trace: {
      correlation_id: 'cid-1',
      symbol: 'SOL',
      stages: [
        { kind: 'price', at: new Date().toISOString(), reached: true, summary: 'PriceEvent SOL', detail: { price: 178.2 } },
        { kind: 'analysis', at: new Date().toISOString(), reached: true, summary: 'Triage Haiku 64', detail: { score: 64 } },
        { kind: 'risk', at: new Date().toISOString(), reached: true, summary: 'Rejeté : floor', detail: { floor: 70 } },
      ],
    },
    correlation_id: 'cid-1',
  };
}
