import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { JournalTable, pnlTone } from '../JournalTable';
import { getJournalDecisions } from '@/lib/mock/journal';

describe('pnlTone', () => {
  it('un pnl nul (flat au horizon) rend neutre, jamais rouge ni vert', () => {
    expect(pnlTone(0)).toBe('text.primary');
  });

  it('un pnl positif rend vert', () => {
    expect(pnlTone(2.4)).toBe('success.main');
  });

  it('un pnl négatif rend rouge', () => {
    expect(pnlTone(-1.1)).toBe('error.main');
  });

  it('un pnl absent (non jugé) rend estompé, pas zéro', () => {
    expect(pnlTone(null)).toBe('text.disabled');
  });
});

describe('JournalTable', () => {
  it('la ligne jr-49 du mock (pnl 0, horizon) affiche « 0% », pas « — » ni « +0% »', () => {
    // Le mock déterministe produit un pnl exactement nul sur la ligne jr-49
    // (branche `horizon` : ni stop ni take-profit touché) — un cas réel du
    // no-op dont la couleur (assertée séparément via pnlTone) ne doit
    // jamais lire comme une perte.
    const page = getJournalDecisions('30d', 50, 0);
    const row = page.rows.find((r) => r.event_id === 'jr-49');
    expect(row?.pnl_pct).toBe(0);
    if (!row) throw new Error('fixture jr-49 introuvable');

    render(<JournalTable rows={[row]} loading={false} onSelect={vi.fn()} />);

    expect(screen.getByText('0%')).toBeInTheDocument();
  });
});
