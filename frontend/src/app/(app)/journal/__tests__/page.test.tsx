import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import JournalPage from '../page';
import { getJournalAttribution, getJournalCalibration, getJournalDecisions } from '@/lib/mock/journal';

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(), usePathname: vi.fn(), useSearchParams: vi.fn(),
}));
vi.mock('@/lib/api/endpoints', () => ({
  journalApi: { decisions: vi.fn(), calibration: vi.fn(), attribution: vi.fn() },
}));

import { journalApi } from '@/lib/api/endpoints';

const decisionsMock = vi.mocked(journalApi.decisions);
const calibrationMock = vi.mocked(journalApi.calibration);
const attributionMock = vi.mocked(journalApi.attribution);
const pushMock = vi.fn();

function setup(decisionsLimit = 50) {
  vi.mocked(useRouter).mockReturnValue({ push: pushMock } as unknown as ReturnType<typeof useRouter>);
  vi.mocked(usePathname).mockReturnValue('/journal');
  vi.mocked(useSearchParams).mockReturnValue(new URLSearchParams('') as unknown as ReturnType<typeof useSearchParams>);
  decisionsMock.mockResolvedValue(getJournalDecisions('30d', decisionsLimit, 0));
  calibrationMock.mockResolvedValue(getJournalCalibration('30d', 70));
  attributionMock.mockResolvedValue(getJournalAttribution('30d'));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><JournalPage /></QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('/journal', () => {
  it('rend les trois panneaux', async () => {
    setup();
    // La section « Décisions jugées » et le sous-titre de page partagent la
    // même phrase (« ... Décisions jugées, calibration de seuil... ») ;
    // le titre de section rend en <h6>, ce qui lève l'ambiguïté.
    expect(await screen.findByRole('heading', { name: /Décisions jugées/ })).toBeInTheDocument();
    expect(screen.getByText(/Calibration de seuil/)).toBeInTheDocument();
    expect(screen.getByText(/Attribution/)).toBeInTheDocument();
  });

  it('un facteur sous min_n rend « — », pas un zéro', async () => {
    setup();
    // liquidity a n=14 < 20 dans le mock → corrélation nulle → tiret
    const liquidityRow = await screen.findByTestId('attribution-liquidity');
    expect(liquidityRow).toHaveTextContent('—');
  });

  it('clic sur une ligne ouvre ?decision=', async () => {
    // Un jeu réduit (2 lignes) tient dans la fenêtre de virtualisation du
    // DataGrid sous jsdom. `findAllByRole('row')` seul ne suffit pas : la
    // ligne d'en-tête porte déjà role="row" et satisfait la requête avant que
    // le virtualiseur ait matérialisé les lignes de données au tick suivant
    // — il faut attendre explicitement qu'une deuxième ligne apparaisse.
    setup(2);
    const rows = await waitFor(() => {
      const found = screen.getAllByRole('row');
      expect(found.length).toBeGreaterThan(1);
      return found;
    });
    fireEvent.click(rows[1]);
    expect(pushMock).toHaveBeenCalled();
    expect(String(pushMock.mock.calls[0][0])).toContain('decision=jr-');
  });
});
