import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { WorkerDecisionsPanel } from '../WorkerDecisionsPanel';
import type { WorkerDecision } from '@/lib/types/domain';

// `opportunity_score` arrives on a 0–1 scale — `map_decision` divides by 100
// and `ScoreChip` multiplies back by 100 for display. 0.84 renders "84".
const LONG_JUSTIFICATION =
  'Le volume sur 24h a progressé de 38% avec un ratio acheteur/vendeur en ' +
  "hausse constante depuis trois jours, ce qui confirme l'intérêt croissant " +
  "des adresses actives. Le funding reste négatif malgré la hausse du prix, " +
  "signe d'un positionnement encore prudent des shorts — un contexte " +
  "favorable à une poursuite du mouvement si le volume se maintient.";

function makeDecision(overrides: Partial<WorkerDecision> = {}): WorkerDecision {
  return {
    id: 'dec-1',
    symbol: 'BTC',
    worker: 'sonnet',
    decision: 'BUY',
    opportunity_score: 0.84,
    confidence: 0.72,
    justification: LONG_JUSTIFICATION,
    escalated: false,
    created_at: '2026-08-01T09:00:00Z',
    ...overrides,
  };
}

const now = new Date('2026-08-01T09:05:00Z').getTime();

describe('WorkerDecisionsPanel', () => {
  it('replie la justification par défaut et l’étend au clic', () => {
    render(<WorkerDecisionsPanel decisions={[makeDecision()]} loading={false} now={now} />);

    const toggle = screen.getByRole('button', { name: /Justification/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    // Collapsed: the clamp is applied via inline style (jsdom doesn't compute
    // visual truncation, so we assert on the style rather than visible text).
    const text = screen.getByText(LONG_JUSTIFICATION);
    expect(text).toHaveStyle({ WebkitLineClamp: '3' });

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(LONG_JUSTIFICATION)).not.toHaveStyle({ WebkitLineClamp: '3' });
  });

  it('le mode bare n’affiche pas le titre de la carte, le mode par défaut si', () => {
    const { unmount } = render(
      <WorkerDecisionsPanel decisions={[makeDecision()]} loading={false} now={now} bare />,
    );
    expect(screen.queryByText('Décisions des workers Claude')).not.toBeInTheDocument();
    unmount();

    render(<WorkerDecisionsPanel decisions={[makeDecision()]} loading={false} now={now} />);
    expect(screen.getByText('Décisions des workers Claude')).toBeInTheDocument();
  });

  it('le bascule de justification est opérable au clavier', () => {
    render(<WorkerDecisionsPanel decisions={[makeDecision()]} loading={false} now={now} />);

    const toggle = screen.getByRole('button', { name: /Justification/ });
    toggle.focus();
    expect(toggle).toHaveFocus();

    fireEvent.keyDown(toggle, { key: 'Enter' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    fireEvent.keyDown(toggle, { key: ' ' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('groupe Sonnet et Haiku séparément, sans titre de groupe vide', () => {
    render(
      <WorkerDecisionsPanel
        decisions={[
          makeDecision({ id: 'dec-sonnet', worker: 'sonnet', symbol: 'BTC' }),
          makeDecision({ id: 'dec-haiku', worker: 'haiku', symbol: 'ETH' }),
        ]}
        loading={false}
        now={now}
      />,
    );

    // "Claude Sonnet" / "Claude Haiku" each render twice: once as the group
    // heading chip, once as the individual decision card's worker chip.
    expect(screen.getAllByText('Claude Sonnet')).toHaveLength(2);
    expect(screen.getAllByText('Claude Haiku')).toHaveLength(2);
    expect(screen.getAllByText(/^1 décision$/)).toHaveLength(2);
  });

  it('n’affiche aucun groupe vide quand un des deux workers est absent', () => {
    render(
      <WorkerDecisionsPanel
        decisions={[makeDecision({ worker: 'sonnet' })]}
        loading={false}
        now={now}
      />,
    );

    expect(screen.getAllByText('Claude Sonnet')).toHaveLength(2);
    expect(screen.queryByText('Claude Haiku')).not.toBeInTheDocument();
  });
});
