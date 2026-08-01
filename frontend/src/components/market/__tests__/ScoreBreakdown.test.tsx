import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScoreBreakdown } from '../ScoreBreakdown';
import type { TokenScore } from '@/lib/types/dossier';

const score: TokenScore = {
  value: 84,
  confidence: 0.62,
  axes: { volume_growth: 0.81, positioning: 0.93 },
  axes_total: 7,
  insufficient_evidence: false,
  computed_at: '2026-08-01T09:12:00Z',
};

describe('ScoreBreakdown', () => {
  it('affiche les axes mesurés avec leur valeur', () => {
    render(<ScoreBreakdown score={score} />);
    expect(screen.getByTestId('axis-positioning')).toHaveTextContent('93');
  });

  it('rend un axe non mesuré en tiret, jamais en zéro', () => {
    render(<ScoreBreakdown score={score} />);
    const cell = screen.getByTestId('axis-fundamentals');
    expect(cell).toHaveTextContent('—');
    expect(cell).not.toHaveTextContent('0');
  });

  it('annonce combien d’axes sont mesurés', () => {
    render(<ScoreBreakdown score={score} />);
    expect(screen.getByText(/2 axes sur 7/)).toBeInTheDocument();
  });

  it('dit que les preuves étaient insuffisantes plutôt que d’afficher un score', () => {
    render(
      <ScoreBreakdown
        score={{
          value: null,
          confidence: null,
          axes: {},
          axes_total: 7,
          insufficient_evidence: true,
          computed_at: '2026-08-01T09:12:00Z',
        }}
      />,
    );
    expect(screen.getByText(/Preuves insuffisantes/)).toBeInTheDocument();
  });

  it('sans score, rend tous les axes en tiret sans planter', () => {
    render(
      <ScoreBreakdown
        score={{
          value: null,
          confidence: null,
          axes: {},
          axes_total: 7,
          insufficient_evidence: false,
          computed_at: null,
        }}
      />,
    );
    expect(screen.getByTestId('axis-volume_growth')).toHaveTextContent('—');
    expect(screen.getByText(/0 axe sur 7/)).toBeInTheDocument();
    expect(screen.queryByText(/Preuves insuffisantes/)).not.toBeInTheDocument();
  });
});
