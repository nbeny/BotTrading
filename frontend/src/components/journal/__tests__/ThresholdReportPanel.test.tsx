import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThresholdReportPanel } from '../ThresholdReportPanel';
import { getThresholdReport } from '@/lib/mock/threshold';

vi.mock('@/lib/api/endpoints', () => ({
  journalApi: { threshold: vi.fn() },
  analysisApi: { requestThresholdScan: vi.fn() },
}));

import { analysisApi, journalApi } from '@/lib/api/endpoints';

const thresholdGet = vi.mocked(journalApi.threshold);
const scanPost = vi.mocked(analysisApi.requestThresholdScan);

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ThresholdReportPanel />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('ThresholdReportPanel', () => {
  it('rend le refus en entier, avec son texte explicatif', async () => {
    thresholdGet.mockResolvedValue(getThresholdReport());
    renderPanel();
    // "positioning" apparaît à la fois dans le tableau de présence et dans le
    // refus (titre + détail) : on scope sur le bloc de refus, qui porte la
    // substance du test.
    const refusal = await screen.findByTestId('threshold-refusal');
    expect(refusal).toHaveTextContent(/positioning/);
    // Le detail du refus doit etre rendu, pas resume
    expect(refusal).toHaveTextContent(/collecte/);
    // Aucun seuil propose quand la garde refuse
    expect(screen.queryByTestId('threshold-proposal')).not.toBeInTheDocument();
  });

  it('affiche le compte brut à côté du pourcentage', async () => {
    thresholdGet.mockResolvedValue(getThresholdReport());
    renderPanel();
    // 1 ligne sur 1 281 511 : le pourcentage seul dirait « 0.0% »
    expect(await screen.findByText(/1 ligne|1 lignes|\(1\)/)).toBeInTheDocument();
  });

  it('état vide honnête quand aucun scan n’a tourné', async () => {
    thresholdGet.mockResolvedValue({
      report: null, report_computed_at: null, status: null, error: null, computed_at: null,
      window_days: null, target_per_day: null, duration_s: null, running: false,
    });
    renderPanel();
    expect(await screen.findByText(/aucun scan/i)).toBeInTheDocument();
  });

  it('dit qu’un scan a échoué plutôt que d’afficher un rapport périmé', async () => {
    thresholdGet.mockResolvedValue({
      report: null, report_computed_at: null, status: 'error', error: 'RuntimeError: stream died',
      computed_at: '2026-08-08T12:00:00+00:00', window_days: 7,
      target_per_day: 200, duration_s: 3.2, running: false,
    });
    renderPanel();
    expect(await screen.findByText(/échoué/i)).toBeInTheDocument();
    expect(screen.getByText(/stream died/)).toBeInTheDocument();
  });

  it('garde le dernier rapport valide visible quand le scan le plus récent a échoué', async () => {
    thresholdGet.mockResolvedValue({
      ...getThresholdReport(),
      status: 'error',
      error: 'RuntimeError: stream died',
      computed_at: '2026-08-08T12:00:00+00:00',
    });
    renderPanel();
    // Le message d'échec ET le rapport (table des axes) doivent être rendus
    // ensemble — plus d'early return qui masquerait le dernier bon rapport.
    expect(await screen.findByText(/échoué/i)).toBeInTheDocument();
    expect(screen.getByText(/stream died/)).toBeInTheDocument();
    expect(await screen.findByTestId('threshold-refusal')).toBeInTheDocument();
    expect(screen.getByText('volume_growth')).toBeInTheDocument();
  });

  it('désactive le bouton et le dit pendant un scan', async () => {
    thresholdGet.mockResolvedValue({ ...getThresholdReport(), running: true });
    renderPanel();
    // Le bouton existe dès le premier rendu (avant que la requête ne
    // resolve) ; attendre le marqueur "en cours" garantit qu'on inspecte
    // l'état une fois les données chargées.
    expect(await screen.findByText(/en cours/i)).toBeInTheDocument();
    const button = screen.getByRole('button', { name: /relancer/i });
    expect(button).toBeDisabled();
  });

  it('déclenche un scan au clic', async () => {
    thresholdGet.mockResolvedValue(getThresholdReport());
    scanPost.mockResolvedValue({ ok: true });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /relancer/i }));
    expect(scanPost).toHaveBeenCalledTimes(1);
  });
});
