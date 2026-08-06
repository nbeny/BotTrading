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
    // funding est mesuré (state bearish) et porte un as_of dans le mock → « mesuré ».
    expect(await screen.findByText(/mesuré/)).toBeInTheDocument();
  });

  it('affiche « fraîcheur inconnue » pour un driver mesuré (voté) sans as_of, jamais « non mesuré »', async () => {
    const regime = getRegime();
    regime.drivers = regime.drivers.map((d) => (d.key === 'funding' ? { ...d, as_of: null } : d));
    regimeGet.mockResolvedValue(regime);
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    await screen.findByText('ACCUMULATION');
    fireEvent.click(screen.getByTestId('driver-funding'));
    expect(await screen.findByText(/fraîcheur inconnue/)).toBeInTheDocument();
    expect(screen.queryByText(/non mesuré/)).not.toBeInTheDocument();
  });

  it('rend REGIME: — quand le régime est null', async () => {
    regimeGet.mockResolvedValue({ ...getRegime(), regime: null, confidence: 0.2 });
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    expect(await screen.findByTestId('regime-label')).toHaveTextContent('—');
  });

  it('affiche kill:on quand trading_enabled est false', async () => {
    regimeGet.mockResolvedValue(getRegime());
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: false, auto_trading_enabled: false });
    renderStrip();
    expect(await screen.findByText('kill:on')).toBeInTheDocument();
  });

  it('affiche kill:— tant que le statut n’a pas encore répondu', async () => {
    statusGet.mockReturnValue(new Promise(() => {}));
    regimeGet.mockResolvedValue(getRegime());
    renderStrip();
    expect(await screen.findByText('kill:—')).toBeInTheDocument();
  });
});
