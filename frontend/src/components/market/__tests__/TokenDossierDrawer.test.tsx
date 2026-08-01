import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TokenDossierDrawer } from '../TokenDossierDrawer';
import type { MarketToken } from '@/lib/types/domain';
import type { TokenDossier } from '@/lib/types/dossier';

// The drawer resolves data through `marketApi` — mock the whole endpoints
// module so neither the dossier query nor `TokenPricePanel`'s own price
// query (rendered unconditionally, bare, once a token is set) hit axios.
vi.mock('@/lib/api/endpoints', () => ({
  marketApi: {
    dossier: vi.fn(),
    prices: vi.fn().mockResolvedValue([]),
  },
}));

import { marketApi } from '@/lib/api/endpoints';

const dossierMock = vi.mocked(marketApi.dossier);

const token: MarketToken = {
  symbol: 'BTC',
  coin_id: 'bitcoin',
  name: 'Bitcoin',
  price_usd: 63500,
  price_change_pct_24h: 4.1,
  volume_24h_usd: 12_000_000,
  liquidity_usd: 3_000_000,
  market_cap_usd: 1_200_000_000_000,
  sentiment_score: 0.2,
  opportunity_score: 0.71,
  is_trending: true,
  updated_at: '2026-08-01T09:00:00Z',
};

// Built from `TokenDossier` (frontend/src/lib/types/dossier.ts) and
// cross-checked against the three `market/dossier*` manifest entries in
// services/api-gateway/app/read_contract.py: `market/dossier` (symbol,
// score, pipeline, decisions, content, exposure), `market/dossier.score`
// (value, confidence, axes, axes_total, insufficient_evidence, computed_at)
// and `market/dossier.pipeline` (reached_stage, blocked_at, block_reason,
// escalated, sonnet_called, sonnet_validated, last_event_at). No disagreement
// found — every field below has a matching manifest key.
const dossier: TokenDossier = {
  symbol: 'BTC',
  score: {
    value: 84,
    confidence: 0.62,
    // `fundamentals` deliberately absent — must render `—`, never `0`.
    axes: { volume_growth: 0.81, positioning: 0.93 },
    axes_total: 7,
    insufficient_evidence: false,
    computed_at: '2026-08-01T09:12:00Z',
  },
  pipeline: {
    reached_stage: 'decision',
    blocked_at: null,
    block_reason: null,
    escalated: true,
    sonnet_called: true,
    sonnet_validated: true,
    last_event_at: '2026-08-01T09:12:00Z',
  },
  decisions: [],
  content: [],
  exposure: { open_positions: [], recent_trades: [] },
};

function renderDrawer(props: Partial<React.ComponentProps<typeof TokenDossierDrawer>> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onClose = vi.fn();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <TokenDossierDrawer token={token} onClose={onClose} now={Date.now()} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('TokenDossierDrawer', () => {
  it('ne rend rien quand token est null', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <TokenDossierDrawer token={null} onClose={vi.fn()} now={Date.now()} />
      </QueryClientProvider>,
    );
    expect(screen.queryByText('BTC')).not.toBeInTheDocument();
  });

  it("affiche l'en-tête (symbole, prix) pendant que la requête est encore en attente", () => {
    // Never resolves within this test — proves the header does not wait on
    // the network.
    dossierMock.mockReturnValue(new Promise(() => {}));
    renderDrawer();

    expect(screen.getByText('BTC')).toBeInTheDocument();
    expect(screen.getByText('$63,500.00')).toBeInTheDocument();
  });

  it('sur un dossier résolu, affiche la décomposition du score avec un axe non mesuré en tiret', async () => {
    dossierMock.mockResolvedValue(dossier);
    renderDrawer();

    await waitFor(() => expect(screen.getByTestId('axis-volume_growth')).toBeInTheDocument());
    expect(screen.getByTestId('axis-volume_growth')).toHaveTextContent('81');
    const fundamentals = screen.getByTestId('axis-fundamentals');
    expect(fundamentals).toHaveTextContent('—');
    expect(fundamentals).not.toHaveTextContent('0');
  });

  it("sur erreur de requête, affiche un message d'erreur et aucun panneau de score", async () => {
    dossierMock.mockRejectedValue(new Error('network down'));
    renderDrawer();

    await waitFor(() =>
      expect(screen.getByText("Le dossier n'a pas pu être chargé.")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Décomposition du score/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('axis-volume_growth')).not.toBeInTheDocument();
  });

  it('le bouton fermer appelle onClose', () => {
    dossierMock.mockReturnValue(new Promise(() => {}));
    const { onClose } = renderDrawer();

    fireEvent.click(screen.getByRole('button', { name: 'Fermer' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
