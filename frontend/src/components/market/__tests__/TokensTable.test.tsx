import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TokensTable } from '../TokensTable';
import type { MarketToken } from '@/lib/types/domain';

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

// 20 tokens — comfortably above VISIBLE_ROWS (15) so the bound is exercised.
function makeManyTokens(): MarketToken[] {
  return Array.from({ length: 20 }, (_, i) =>
    token({
      symbol: `TOK${i}`,
      name: `Token ${i}`,
      opportunity_score: i / 20,
    }),
  );
}

describe('TokensTable', () => {
  it('bounds the visible rows to 15 when more tokens exist, and "Voir les N" reveals the rest', () => {
    const tokens = makeManyTokens();
    render(
      <TokensTable tokens={tokens} loading={false} selectedSymbol={null} onSelect={vi.fn()} />,
    );

    // DataGrid virtualises rows against the scroll container, which jsdom
    // gives zero real layout to — so an exact "15 rows in the DOM" count is
    // not something virtualisation guarantees here. What IS guaranteed
    // regardless of virtualisation: `rows` fed to the grid is `view.slice(0,
    // 15)`, so the caption reflects that bound, and a token past the 15th
    // (by score, ascending symbols TOK0..TOK4 have the lowest scores and are
    // cut) must not be in the document at all.
    expect(screen.getByText('15 sur 20')).toBeInTheDocument();
    expect(screen.queryByText('TOK0')).not.toBeInTheDocument();
    expect(screen.queryByText('TOK4')).not.toBeInTheDocument();
    // Highest-scoring token (TOK19) is within the first 15 and must render.
    expect(screen.getByText('TOK19')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Voir les 20' }));

    expect(screen.getByText('20 sur 20')).toBeInTheDocument();
    expect(screen.getByText('TOK0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Réduire' })).toBeInTheDocument();
  });

  it('typing in the search narrows the rows', () => {
    const tokens = makeManyTokens();
    render(
      <TokensTable tokens={tokens} loading={false} selectedSymbol={null} onSelect={vi.fn()} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Rechercher un token…'), {
      target: { value: 'TOK1' },
    });

    // TOK1, TOK10..TOK19 all match the substring "TOK1" — 11 tokens.
    expect(screen.getByText('11 sur 20')).toBeInTheDocument();
    expect(screen.getByText('TOK1')).toBeInTheDocument();
    expect(screen.getByText('TOK10')).toBeInTheDocument();
    expect(screen.queryByText('TOK2')).not.toBeInTheDocument();
    // The "Voir les N" toggle only makes sense with no active query.
    expect(screen.queryByRole('button', { name: /Voir les/ })).not.toBeInTheDocument();
  });

  it('clicking a row calls onSelect with that symbol', () => {
    const tokens = [token({ symbol: 'BTC' }), token({ symbol: 'ETH', name: 'Ethereum' })];
    const onSelect = vi.fn();
    render(
      <TokensTable tokens={tokens} loading={false} selectedSymbol={null} onSelect={onSelect} />,
    );

    const ethCell = screen.getByText('Ethereum');
    const row = ethCell.closest('[role="row"]');
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByText('ETH'));

    expect(onSelect).toHaveBeenCalledWith('ETH');
  });

  it('shows the loading skeleton', () => {
    render(<TokensTable tokens={[]} loading onSelect={vi.fn()} selectedSymbol={null} />);
    expect(screen.queryByRole('grid')).not.toBeInTheDocument();
  });

  it('shows the empty state when there are no tokens', () => {
    render(
      <TokensTable tokens={[]} loading={false} onSelect={vi.fn()} selectedSymbol={null} />,
    );
    expect(screen.getByText('Aucun token disponible.')).toBeInTheDocument();
  });
});
