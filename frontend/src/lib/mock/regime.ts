import type { MarketRegime } from '@/lib/types/regime';

/** market_sentiment volontairement null : exerce le rendu « — » du strip,
 *  comme le mock dossier laisse `fundamentals` absent. */
export function getRegime(): MarketRegime {
  return {
    regime: 'ACCUMULATION',
    confidence: 0.8,
    computed_at: new Date().toISOString(),
    drivers: [
      { key: 'funding', value: 0.00013, state: 'bearish', detail: 'médiane funding +0.000130/8h (Binance, univers suivi) : crowded-long. Contrarien : > +0.0001 → bearish, < -0.0001 → bullish.', as_of: new Date(Date.now() - 240_000).toISOString() },
      { key: 'oi_delta', value: 6.2, state: 'bullish', detail: 'médiane ΔOI 24h +6.2% (majors Binance), prix BTC 24h +2.1% : levier suit la hausse. Seuil ±5%.', as_of: new Date(Date.now() - 240_000).toISOString() },
      { key: 'market_sentiment', value: null, state: null, detail: 'lecture market-wide indisponible (cadence irrégulière mesurée : médiane 19 min, p95 71 min)', as_of: null },
      { key: 'btc_dominance', value: -0.7, state: 'bullish', detail: 'BTC.D 53.4% (univers suivi ~200 tokens, pas le marché entier), dérive 7j -0.70 pt : rotation vers les alts. Seuil ±0.5 pt.', as_of: new Date(Date.now() - 3_600_000).toISOString() },
      { key: 'breadth', value: 0.64, state: 'bullish', detail: '64% des 187 tokens suivis en hausse sur 24h. Seuils : > 60% bullish, < 40% bearish.', as_of: new Date(Date.now() - 3_600_000).toISOString() },
    ],
  };
}
