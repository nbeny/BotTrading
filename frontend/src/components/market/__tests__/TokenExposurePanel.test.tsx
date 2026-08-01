import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TokenExposurePanel } from '../TokenExposurePanel';
import type { TokenExposure } from '@/lib/types/dossier';
import type { Position, Trade } from '@/lib/types/domain';

const position: Position = {
  position_id: 'pos-1',
  symbol: 'BTC',
  direction: 'long',
  quantity: 0.42,
  entry_price: 61000,
  current_price: 63500,
  value_usd: 26670,
  unrealized_pnl_usd: 1050,
  unrealized_pnl_pct: 4.1,
  stop_loss: 58000,
  take_profit: 70000,
  protected: true,
  opened_at: '2026-07-30T10:00:00Z',
  mode: 'live',
};

const trade: Trade = {
  trade_id: 'trade-1',
  symbol: 'BTC',
  side: 'buy',
  order_type: 'market',
  price: 61000,
  quantity: 0.42,
  cost_usd: 25620,
  fee_usd: 12.5,
  pnl_usd: null,
  status: 'filled',
  mode: 'live',
  executed_at: '2026-07-30T10:00:00Z',
};

describe('TokenExposurePanel', () => {
  it('sans position ni trade, rend l’état vide', () => {
    const exposure: TokenExposure = { open_positions: [], recent_trades: [] };
    render(<TokenExposurePanel exposure={exposure} />);
    expect(screen.getByText(/Aucune position ouverte, aucun trade récent/)).toBeInTheDocument();
  });

  it('une position ouverte affiche direction, quantité, prix d’entrée et P&L latent', () => {
    const exposure: TokenExposure = { open_positions: [position], recent_trades: [] };
    render(<TokenExposurePanel exposure={exposure} />);
    expect(screen.getByText(/LONG/)).toBeInTheDocument();
    expect(screen.getByText(/0\.42/)).toBeInTheDocument();
    expect(screen.getByText(/61,000/)).toBeInTheDocument();
    expect(screen.getByText(/\+4\.10%/)).toBeInTheDocument();
  });

  it('des trades sans position ouverte affichent quand même quelque chose (pas l’état vide)', () => {
    const exposure: TokenExposure = { open_positions: [], recent_trades: [trade] };
    render(<TokenExposurePanel exposure={exposure} />);
    expect(
      screen.queryByText(/Aucune position ouverte, aucun trade récent/),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/1 trade sur ce symbole/)).toBeInTheDocument();
  });

  it('un P&L négatif s’affiche comme négatif, pas comme une valeur absolue', () => {
    const losingPosition: Position = { ...position, unrealized_pnl_pct: -3.25 };
    const exposure: TokenExposure = { open_positions: [losingPosition], recent_trades: [] };
    render(<TokenExposurePanel exposure={exposure} />);
    expect(screen.getByText(/-3\.25%/)).toBeInTheDocument();
    expect(screen.queryByText(/\+3\.25%/)).not.toBeInTheDocument();
  });
});
