import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PipelineVerdictPanel } from '../PipelineVerdictPanel';
import type { PipelineVerdict } from '@/lib/types/dossier';

const base: PipelineVerdict = {
  reached_stage: 'risk',
  blocked_at: null,
  block_reason: null,
  escalated: true,
  sonnet_called: true,
  sonnet_validated: true,
  last_event_at: '2026-08-01T09:12:00Z',
};

describe('PipelineVerdictPanel', () => {
  it('nomme l’étage de blocage et son motif', () => {
    render(
      <PipelineVerdictPanel
        verdict={{
          ...base,
          blocked_at: 'risk',
          block_reason: 'score_below_threshold',
        }}
      />,
    );
    // « Risque » apparaît deux fois ici (chip « Atteint » + « Bloqué à ») —
    // c'est correct, l'étage atteint et l'étage de blocage coïncident. On
    // vérifie la présence, pas l'unicité.
    expect(screen.getAllByText(/Risque/).length).toBeGreaterThan(0);
    expect(screen.getByText(/score_below_threshold/)).toBeInTheDocument();
  });

  it('sans blocage observé, ne prétend pas que le signal est passé', () => {
    render(<PipelineVerdictPanel verdict={base} />);
    expect(screen.getByText(/Aucun blocage observé/)).toBeInTheDocument();
  });

  it('sans historique, rend un tiret', () => {
    render(
      <PipelineVerdictPanel
        verdict={{
          reached_stage: null,
          blocked_at: null,
          block_reason: null,
          escalated: null,
          sonnet_called: null,
          sonnet_validated: null,
          last_event_at: null,
        }}
      />,
    );
    expect(screen.getByText(/Jamais analysé/)).toBeInTheDocument();
  });

  it('une escalade inconnue ne s’affiche pas comme « non escaladé »', () => {
    // Cas du rejet sans ligne de journal : on ignore si Haiku avait escaladé.
    // Ne rien afficher est honnête ; afficher « non » serait une affirmation
    // que personne n'a mesurée.
    render(
      <PipelineVerdictPanel
        verdict={{ ...base, escalated: null, sonnet_called: null, sonnet_validated: null }}
      />,
    );
    expect(screen.queryByText(/Escaladé/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Sonnet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/non/i)).not.toBeInTheDocument();
  });

  it('distingue un Sonnet non validé d’un verdict Sonnet inconnu', () => {
    const { rerender } = render(
      <PipelineVerdictPanel verdict={{ ...base, sonnet_validated: false }} />,
    );
    expect(screen.getByText(/Sonnet — non validé/)).toBeInTheDocument();

    rerender(<PipelineVerdictPanel verdict={{ ...base, sonnet_validated: null }} />);
    expect(screen.getByText(/Sonnet — verdict inconnu/)).toBeInTheDocument();
  });
});
