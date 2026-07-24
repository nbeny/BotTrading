import { pick, rand, round, UNIVERSE } from './universe';
import type { DecisionTrace } from '@/lib/types/content';

function iso(offsetMs: number) { return new Date(Date.now() + offsetMs).toISOString(); }

export function getTrace(cid: string): DecisionTrace {
  const t = pick(UNIVERSE);
  const price = round(t.price, 2);
  return {
    correlation_id: cid,
    symbol: t.symbol,
    stages: [
      { kind: 'price', at: iso(-42000), reached: true, summary: `Tick prix ${t.symbol} $${price}`,
        detail: { source: 'coingecko', price_usd: price, change_24h_pct: round(rand(2, 9), 2) } },
      { kind: 'sentiment', at: iso(-38000), reached: true, summary: 'Sentiment social/news agrégé',
        detail: { score: round(rand(0.2, 0.7), 2), model: 'ElKulako/cryptobert', sample: Math.floor(rand(30, 200)) } },
      { kind: 'analysis', at: iso(-30000), reached: true, summary: 'Haiku — triage (escalade)',
        detail: { opportunity_score: Math.floor(rand(78, 92)), confidence: round(rand(0.7, 0.95), 2), escalate: true } },
      { kind: 'decision', at: iso(-22000), reached: true, summary: 'Sonnet — LONG validé',
        detail: { direction: 'long', confidence: round(rand(0.72, 0.93), 2), ai_validated: true } },
      { kind: 'risk', at: iso(-12000), reached: true, summary: 'Risque — sizing & protection',
        detail: { size_pct: round(rand(2, 6), 1), stop_loss: round(price * 0.95, 2), take_profit: round(price * 1.11, 2), rr: round(rand(1.8, 2.8), 2) } },
      { kind: 'order', at: iso(-6000), reached: true, summary: 'Ordre exécuté (paper)',
        detail: { status: 'filled', price, fee: round(rand(1, 12), 2), mode: 'dry_run' } },
    ],
  };
}
