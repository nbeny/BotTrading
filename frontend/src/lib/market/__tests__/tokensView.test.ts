import { describe, expect, it } from 'vitest';
import { filterAndSortTokens } from '../tokensView';
import type { MarketToken } from '@/lib/types/domain';

// `opportunity_score` is on a 0–1 scale — `map_token` divides the backend's
// 0-100 value by 100 (ScoreChip multiplies back by 100 for display). Fixtures
// must match that scale or a sort-key bug would go unnoticed here.
function token(over: Partial<MarketToken>): MarketToken {
  return {
    symbol: 'BTC',
    coin_id: 'bitcoin',
    name: 'Bitcoin',
    price_usd: 65000,
    price_change_pct_24h: 1.2,
    volume_24h_usd: 32_000_000_000,
    liquidity_usd: 1_200_000_000,
    market_cap_usd: 1_280_000_000_000,
    sentiment_score: 0.1,
    opportunity_score: 0.6,
    is_trending: false,
    updated_at: '2026-08-01T00:00:00Z',
    ...over,
  };
}

// A fresh array per call, deliberately — the whole point of the mutation test
// below is that `filterAndSortTokens` must not sort in place. A single
// module-level array shared across `it` blocks would let an earlier test's
// in-place sort leak into a later test's "before" snapshot and mask the bug
// (verified while writing this test: see the report on the deliberate
// breakage in the task writeup).
function makeTokens(): MarketToken[] {
  return [
    token({ symbol: 'BTC', name: 'Bitcoin', opportunity_score: 0.6 }),
    token({ symbol: 'SOL', name: 'Solana', opportunity_score: 0.84 }),
    token({ symbol: 'ETH', name: 'Ethereum', opportunity_score: 0.79 }),
  ];
}

describe('filterAndSortTokens', () => {
  it('sorts descending by the chosen key', () => {
    const out = filterAndSortTokens(makeTokens(), '', 'opportunity_score');
    expect(out.map((t) => t.symbol)).toEqual(['SOL', 'ETH', 'BTC']);
  });

  it('sorts descending by a different key', () => {
    const withVolume = [
      token({ symbol: 'BTC', volume_24h_usd: 100 }),
      token({ symbol: 'SOL', volume_24h_usd: 300 }),
      token({ symbol: 'ETH', volume_24h_usd: 200 }),
    ];
    const out = filterAndSortTokens(withVolume, '', 'volume_24h_usd');
    expect(out.map((t) => t.symbol)).toEqual(['SOL', 'ETH', 'BTC']);
  });

  it('filters on symbol, case-insensitively', () => {
    const tokens = makeTokens();
    const out = filterAndSortTokens(tokens, 'sol', 'opportunity_score');
    expect(out.map((t) => t.symbol)).toEqual(['SOL']);

    const outUpper = filterAndSortTokens(tokens, 'SOL', 'opportunity_score');
    expect(outUpper.map((t) => t.symbol)).toEqual(['SOL']);
  });

  it('filters on name', () => {
    const out = filterAndSortTokens(makeTokens(), 'ether', 'opportunity_score');
    expect(out.map((t) => t.symbol)).toEqual(['ETH']);
  });

  it('does not mutate the input array', () => {
    const tokens = makeTokens();
    const before = tokens.map((t) => t.symbol);
    filterAndSortTokens(tokens, '', 'opportunity_score');
    expect(tokens.map((t) => t.symbol)).toEqual(before);
  });
});
