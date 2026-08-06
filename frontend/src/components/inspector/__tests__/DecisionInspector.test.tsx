import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { DecisionInspector } from '../DecisionInspector';
import { getExplain } from '@/lib/mock/journal';

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(),
  usePathname: vi.fn(),
  useSearchParams: vi.fn(),
}));
vi.mock('@/lib/api/endpoints', () => ({ explainApi: { get: vi.fn() } }));

import { explainApi } from '@/lib/api/endpoints';

const routerMock = vi.mocked(useRouter);
const pathnameMock = vi.mocked(usePathname);
const searchParamsMock = vi.mocked(useSearchParams);
const explainGet = vi.mocked(explainApi.get);

function setup(search: string) {
  routerMock.mockReturnValue({ push: vi.fn() } as unknown as ReturnType<typeof useRouter>);
  pathnameMock.mockReturnValue('/command');
  searchParamsMock.mockReturnValue(new URLSearchParams(search) as unknown as ReturnType<typeof useSearchParams>);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DecisionInspector />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('DecisionInspector', () => {
  it('reste fermé sans ?decision=', () => {
    setup('');
    expect(screen.queryByText(/Inspecteur/)).not.toBeInTheDocument();
    expect(explainGet).not.toHaveBeenCalled();
  });

  it('affiche le score brut 0-100 et les axes absents en « — »', async () => {
    explainGet.mockResolvedValue(getExplain('jr-1'));
    setup('decision=jr-1');
    expect(await screen.findByText('64')).toBeInTheDocument();        // jamais 0.64
    // fundamentals et developer_activity absents du mock → deux axes en tiret
    expect((await screen.findAllByText(/absent, exclu du score/)).length).toBeGreaterThanOrEqual(2);
    // /Triage Haiku/ seul est ambigu : le mock's trace timeline contient aussi
    // un stage résumé "Triage Haiku 64" — on vise le qualificatif qui étiquette
    // la section comme namespace disjoint des axes, propre à l'en-tête.
    expect(screen.getByText(/facteurs de triage — distincts des axes/)).toBeInTheDocument();
  });

  it('affiche une erreur propre sur id inconnu', async () => {
    explainGet.mockRejectedValue(new Error('404'));
    setup('decision=unknown');
    expect(await screen.findByText(/introuvable|échoué/i)).toBeInTheDocument();
  });

  it('journal-only (axes vides, insufficient_evidence false) retombe sur le message de fallback', async () => {
    // Correction de contrat : « pas de ligne de décision » (insufficient_evidence)
    // et « rejetée avant scoring » (axes: {}) sont deux cas distincts qui
    // partagent le même fallback — ce test épingle la branche axes vides seule.
    explainGet.mockResolvedValue({
      ...getExplain('jr-2'),
      score: { ...getExplain('jr-2').score, value: null, axes: {}, insufficient_evidence: false },
    });
    setup('decision=jr-2');
    expect(await screen.findByText(/breakdown indisponible/)).toBeInTheDocument();
  });
});
