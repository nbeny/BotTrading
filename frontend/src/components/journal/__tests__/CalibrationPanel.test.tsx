import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CalibrationPanel } from '../CalibrationPanel';
import { getJournalCalibration } from '@/lib/mock/journal';

vi.mock('@/lib/api/endpoints', () => ({
  journalApi: { calibration: vi.fn() },
}));

import { journalApi } from '@/lib/api/endpoints';

const calibrationMock = vi.mocked(journalApi.calibration);

function setup() {
  calibrationMock.mockImplementation((threshold) => Promise.resolve(getJournalCalibration('30d', threshold)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}><CalibrationPanel window="30d" /></QueryClientProvider>,
  );
  const root = utils.container.querySelector('.MuiSlider-root') as HTMLElement;
  // jsdom gives every element a zero-size layout box; the real MUI Slider
  // derives the dragged value from `getBoundingClientRect()`, so a fixed,
  // non-zero rect is required to turn a `clientX` into a deterministic value.
  vi.spyOn(root, 'getBoundingClientRect').mockReturnValue({
    width: 200, height: 4, top: 0, left: 0, bottom: 4, right: 200, x: 0, y: 0, toJSON: () => {},
  } as DOMRect);
  return { ...utils, root };
}

afterEach(() => vi.clearAllMocks());

describe('CalibrationPanel — slider commit-only', () => {
  it('drague sans relâcher ne requête pas le nouveau seuil ; relâcher (commit) le requête', async () => {
    const { root } = setup();
    await screen.findByText(/Seuil simulé : 70/);
    calibrationMock.mockClear(); // ne garder que les appels post-montage

    // mousedown seul = onChange (drag en cours), jamais onChangeCommitted :
    // la position du pointeur à 170/200 → valeur 85 sur l'échelle 0-100.
    fireEvent.mouseDown(root, { clientX: 170, clientY: 2, button: 0 });
    expect(await screen.findByText(/Seuil simulé : 85/)).toBeInTheDocument();
    expect(calibrationMock).not.toHaveBeenCalledWith(85, '30d');

    // mouseup (sur document, où useSlider écoute) = commit.
    fireEvent.mouseUp(document, { clientX: 170, clientY: 2 });
    await waitFor(() => expect(calibrationMock).toHaveBeenCalledWith(85, '30d'));
  });
});
