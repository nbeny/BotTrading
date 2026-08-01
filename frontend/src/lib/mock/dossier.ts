import type { TokenDossier } from '@/lib/types/dossier';
import { getDecisions, getNews, getPositions, getToken, getTrades } from './store';

/**
 * Dossier factice pour un symbole.
 *
 * `fundamentals` est volontairement **absent** de `axes` : c'est le seul cas
 * que le développement front ne verrait jamais autrement, et c'est celui dont
 * le rendu (`—`, pas `0`) est le plus facile à casser sans s'en apercevoir.
 *
 * `score.value` est mis à l'échelle 0-100 (`token.opportunity_score * 100`) :
 * le store du mock garde `MarketToken.opportunity_score` sur 0-1 (voir
 * `store.ts`, cohérent avec `read_api.py` qui divise par 100 pour ce même
 * endpoint), mais `build_score` côté backend (`dossier.py`) renvoie
 * `decision.opportunity_score` tel quel — un entier 0-100
 * (`decision-engine/app/scoring.py`). Copier la valeur brute du store ici
 * afficherait « 0.62 / 100 » au lieu de « 62 / 100 » : deux endpoints du même
 * mock en désaccord sur l'échelle d'un même concept, exactement le genre de
 * défaut que ce dossier existe pour éviter d'introduire.
 */
export function getDossier(symbol: string): TokenDossier | null {
  const sym = symbol.toUpperCase();
  const token = getToken(sym);
  if (!token) return null;

  return {
    symbol: sym,
    score: {
      value: Math.round(token.opportunity_score * 100),
      confidence: 0.62,
      axes: {
        volume_growth: 0.81,
        social_score: 0.74,
        news_score: 0.6,
        market_trend: 0.88,
        liquidity_score: 0.7,
        positioning: 0.93,
        // fundamentals : non mesuré — clé absente exprès, voir la docstring
      },
      axes_total: 7,
      insufficient_evidence: false,
      computed_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    },
    pipeline: {
      reached_stage: 'risk',
      blocked_at: 'risk',
      block_reason: 'score_below_threshold',
      escalated: true,
      sonnet_called: true,
      sonnet_validated: false,
      last_event_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    },
    decisions: getDecisions(30).filter((d) => d.symbol === sym),
    content: getNews(50).filter((n) => n.symbols.includes(sym)),
    exposure: {
      open_positions: getPositions().filter((p) => p.symbol === sym),
      recent_trades: getTrades(50).filter((t) => t.symbol === sym),
    },
  };
}
