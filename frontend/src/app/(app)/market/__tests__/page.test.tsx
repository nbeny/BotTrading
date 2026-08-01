import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MarketToken } from '@/lib/types/domain';

// `useSearchParams`/`useRouter` are mocked as bare `vi.fn()`s and given a
// concrete return value inside each test via `renderPage` — never shared
// across tests, so no test can pass because an earlier test's mock leaked in.
vi.mock('next/navigation', () => ({
  useRouter: vi.fn(),
  useSearchParams: vi.fn(),
}));

// The page calls `marketApi.tokens/news/decisions` directly, and mounts
// `TokenDossierDrawer` (which calls `marketApi.dossier`) and, once a token is
// selected, `TokenPricePanel` inside it (which calls `marketApi.prices`).
// Mocking the whole module keeps every one of those off axios.
vi.mock('@/lib/api/endpoints', () => ({
  marketApi: {
    tokens: vi.fn(),
    news: vi.fn(),
    decisions: vi.fn(),
    dossier: vi.fn(),
    prices: vi.fn(),
  },
}));

import { useRouter, useSearchParams } from 'next/navigation';
import { marketApi } from '@/lib/api/endpoints';
import MarketPage from '../page';

const tokensMock = vi.mocked(marketApi.tokens);
const newsMock = vi.mocked(marketApi.news);
const decisionsMock = vi.mocked(marketApi.decisions);
const dossierMock = vi.mocked(marketApi.dossier);
const pricesMock = vi.mocked(marketApi.prices);
const useRouterMock = vi.mocked(useRouter);
const useSearchParamsMock = vi.mocked(useSearchParams);

function makeToken(overrides: Partial<MarketToken> = {}): MarketToken {
  return {
    symbol: 'SOL',
    coin_id: 'solana',
    name: 'Solana',
    price_usd: 150,
    price_change_pct_24h: 2.5,
    volume_24h_usd: 1_000_000,
    liquidity_usd: 500_000,
    market_cap_usd: 60_000_000_000,
    sentiment_score: 0.4,
    opportunity_score: 0.7,
    is_trending: false,
    updated_at: '2026-08-01T09:00:00Z',
    ...overrides,
  };
}

function renderPage(search: string) {
  const pushMock = vi.fn();
  useRouterMock.mockReturnValue({ push: pushMock } as unknown as ReturnType<typeof useRouter>);
  useSearchParamsMock.mockReturnValue(
    new URLSearchParams(search) as unknown as ReturnType<typeof useSearchParams>,
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MarketPage />
    </QueryClientProvider>,
  );
  return { ...utils, pushMock };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('MarketPage', () => {
  it('sans ?token=, le drawer est fermé', async () => {
    tokensMock.mockResolvedValue([makeToken()]);
    newsMock.mockResolvedValue([]);
    decisionsMock.mockResolvedValue([]);
    renderPage('');

    await waitFor(() => expect(screen.getByText('SOL')).toBeInTheDocument());

    // The drawer's close button only renders inside its `{token && (...)}`
    // block, so its absence proves no token is selected — not just that the
    // Drawer's `open` prop happens to be false.
    expect(screen.queryByRole('button', { name: 'Fermer' })).not.toBeInTheDocument();
    expect(dossierMock).not.toHaveBeenCalled();
  });

  it('avec ?token=SOL et SOL parmi les tokens chargés, le drawer ouvre sur SOL', async () => {
    tokensMock.mockResolvedValue([makeToken({ symbol: 'SOL' })]);
    newsMock.mockResolvedValue([]);
    decisionsMock.mockResolvedValue([]);
    // Never resolves: proves the drawer header renders from the already-loaded
    // token, not from the dossier request.
    dossierMock.mockReturnValue(new Promise(() => {}));
    pricesMock.mockReturnValue(new Promise(() => {}));
    renderPage('token=SOL');

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Fermer' })).toBeInTheDocument(),
    );
    expect(dossierMock).toHaveBeenCalledWith('SOL');
  });

  it("un clic sur une ligne du tableau appelle router.push avec /market?token=<symbole>", async () => {
    tokensMock.mockResolvedValue([makeToken({ symbol: 'ETH', name: 'Ethereum' })]);
    newsMock.mockResolvedValue([]);
    decisionsMock.mockResolvedValue([]);
    const { pushMock } = renderPage('');

    await waitFor(() => expect(screen.getByText('Ethereum')).toBeInTheDocument());
    const cell = screen.getByText('ETH');
    const row = cell.closest('[role="row"]');
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByText('ETH'));

    expect(pushMock).toHaveBeenCalledWith('/market?token=ETH', { scroll: false });
  });

  it("ne rend pas LiveEventStream sur cette page", async () => {
    tokensMock.mockResolvedValue([]);
    newsMock.mockResolvedValue([]);
    decisionsMock.mockResolvedValue([]);
    renderPage('');

    await waitFor(() =>
      expect(screen.getByText('Aucun token disponible.')).toBeInTheDocument(),
    );

    // These strings are LiveEventStream's own default title/subtitle
    // (frontend/src/components/command/LiveEventStream.tsx) — no other
    // component on this page renders them. Their absence proves the stream
    // is not mounted, rather than merely being empty.
    expect(screen.queryByText('Flux temps réel')).not.toBeInTheDocument();
    expect(screen.queryByText(/lignée décisionnelle/)).not.toBeInTheDocument();
  });
});
