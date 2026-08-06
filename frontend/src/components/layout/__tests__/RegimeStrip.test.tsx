import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RegimeStrip } from '../RegimeStrip';
import { getRegime } from '@/lib/mock/regime';

vi.mock('@/lib/api/endpoints', () => ({
  regimeApi: { get: vi.fn() },
  tradingApi: { status: vi.fn() },
}));

import { regimeApi, tradingApi } from '@/lib/api/endpoints';

const regimeGet = vi.mocked(regimeApi.get);
const statusGet = vi.mocked(tradingApi.status);

function renderStrip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RegimeStrip />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('RegimeStrip', () => {
  it('affiche le régime, les drivers et « — » pour un driver non mesuré', async () => {
    regimeGet.mockResolvedValue(getRegime());
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    expect(await screen.findByText('ACCUMULATION')).toBeInTheDocument();
    // market_sentiment est null dans le mock → sa cellule rend un tiret
    expect(screen.getByTestId('driver-market_sentiment')).toHaveTextContent('—');
    expect(screen.getByText(/dry_run/i)).toBeInTheDocument();
  });

  it('ouvre le popover de règle au clic sur un driver', async () => {
    regimeGet.mockResolvedValue(getRegime());
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    await screen.findByText('ACCUMULATION');
    fireEvent.click(screen.getByTestId('driver-funding'));
    expect(await screen.findByText(/Contrarien/)).toBeInTheDocument();
  });

  it('rend REGIME: — quand le régime est null', async () => {
    regimeGet.mockResolvedValue({ ...getRegime(), regime: null, confidence: 0.2 });
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    expect(await screen.findByTestId('regime-label')).toHaveTextContent('—');
  });
});
