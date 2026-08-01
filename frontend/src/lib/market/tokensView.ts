import type { MarketToken } from '@/lib/types/domain';

export type TokenSortKey =
  | 'opportunity_score'
  | 'price_change_pct_24h'
  | 'volume_24h_usd'
  | 'liquidity_usd';

export const SORT_LABELS: Record<TokenSortKey, string> = {
  opportunity_score: 'Score',
  price_change_pct_24h: 'Variation 24h',
  volume_24h_usd: 'Volume 24h',
  liquidity_usd: 'Liquidité',
};

/**
 * Vue triée et filtrée du tableau des tokens.
 *
 * Le filtre est client : la liste entière est déjà en mémoire (un seul
 * `GET /market/tokens` l'alimente), donc chercher n'appelle pas le réseau.
 * Rend toujours un nouveau tableau — `Array.sort` mute en place, et la source
 * vient d'un cache TanStack Query qu'on ne doit pas réordonner.
 */
export function filterAndSortTokens(
  tokens: MarketToken[],
  query: string,
  sortKey: TokenSortKey,
): MarketToken[] {
  const q = query.trim().toLowerCase();
  const filtered = q
    ? tokens.filter(
        (t) =>
          t.symbol.toLowerCase().includes(q) || t.name.toLowerCase().includes(q),
      )
    : tokens;
  return [...filtered].sort((a, b) => b[sortKey] - a[sortKey]);
}
