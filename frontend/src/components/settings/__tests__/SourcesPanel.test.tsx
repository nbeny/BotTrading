import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SourcesPanel } from '../SourcesPanel';
import type { CollectorRuntime, SourceStatus } from '@/lib/api/endpoints';

vi.mock('@/lib/api/endpoints', () => ({
  collectorsApi: {
    runtime: vi.fn(),
    setRuntime: vi.fn(),
    aiQuota: vi.fn(),
  },
}));

// eslint-disable-next-line import/first
import { collectorsApi } from '@/lib/api/endpoints';

const api = vi.mocked(collectorsApi);

/** Recent enough that the reading counts as live (threshold is 15 min). */
const fresh = () => new Date(Date.now() - 60_000).toISOString();

function status(over: Partial<SourceStatus> = {}): SourceStatus {
  return { ok: true, reason: null, channels: {}, updated_at: fresh(), ...over };
}

function runtime(over: Partial<CollectorRuntime> = {}): CollectorRuntime {
  return {
    social_enabled: true,
    news_enabled: true,
    platforms: {},
    known_platforms: { social: ['bluesky', 'telegram'], news: ['rss'] },
    telegram_channels: ['bwenews', 'whalechart'],
    source_status: { telegram: status() },
    ...over,
  };
}

/** Renders the panel and waits for the first load to land. */
async function mount(rt: CollectorRuntime = runtime()) {
  api.runtime.mockResolvedValue(rt);
  api.aiQuota.mockResolvedValue({ paused: false, resume_at: null, workers: [] });
  api.setRuntime.mockResolvedValue(rt);
  render(<SourcesPanel />);
  await screen.findByText('Canaux Telegram');
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SourcesPanel — éditeur de canaux Telegram', () => {
  it('affiche les canaux configurés', async () => {
    await mount();
    expect(screen.getByText('bwenews')).toBeInTheDocument();
    expect(screen.getByText('whalechart')).toBeInTheDocument();
  });

  it('ajoute un canal en postant la liste complète, pas seulement la nouvelle entrée', async () => {
    await mount();

    fireEvent.change(screen.getByLabelText('Ajouter un canal'), {
      target: { value: '@NewDesk' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter' }));

    await waitFor(() =>
      expect(api.setRuntime).toHaveBeenCalledWith({
        telegram_channels: ['bwenews', 'whalechart', '@NewDesk'],
      }),
    );
  });

  it('vide le champ après un ajout accepté', async () => {
    await mount();
    const input = screen.getByLabelText('Ajouter un canal') as HTMLInputElement;

    fireEvent.change(input, { target: { value: 'newdesk' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter' }));

    await waitFor(() => expect(input.value).toBe(''));
  });

  it('retirer le dernier canal poste une liste vide : purger est une action légitime', async () => {
    await mount(runtime({ telegram_channels: ['bwenews'] }));

    fireEvent.click(screen.getByTitle('Retirer bwenews'));

    await waitFor(() => expect(api.setRuntime).toHaveBeenCalledWith({ telegram_channels: [] }));
  });

  it('rend un état vide explicite plutôt qu’un blanc', async () => {
    await mount(runtime({ telegram_channels: [] }));
    expect(screen.getByText(/Aucun canal configuré/)).toBeInTheDocument();
  });

  it('remonte le refus du serveur (422) à l’opérateur', async () => {
    await mount();
    api.setRuntime.mockRejectedValueOnce(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 422, data: { detail: 'invite links are not supported' } },
      }),
    );

    fireEvent.change(screen.getByLabelText('Ajouter un canal'), {
      target: { value: 'https://t.me/+secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter' }));

    expect(await screen.findByText('invite links are not supported')).toBeInTheDocument();
    // Le champ garde la saisie refusée : elle est à corriger, pas à retaper.
    expect((screen.getByLabelText('Ajouter un canal') as HTMLInputElement).value).toBe(
      'https://t.me/+secret',
    );
  });
});

describe('SourcesPanel — santé de la source Telegram', () => {
  it('signale un défaut quand ok vaut false, avec sa raison', async () => {
    await mount(
      runtime({
        source_status: {
          telegram: status({ ok: false, reason: 'all 2 configured channels are unreadable' }),
        },
      }),
    );

    expect(screen.getByTestId('telegram-health')).toHaveTextContent('Source en défaut');
    expect(screen.getByTestId('telegram-health-reason')).toHaveTextContent(
      'all 2 configured channels are unreadable',
    );
  });

  it('ne signale PAS de défaut quand ok vaut true avec une raison : une raison n’est pas un symptôme', async () => {
    await mount(
      runtime({
        source_status: {
          telegram: status({ ok: true, reason: 'rate-limited by Telegram, resuming in 42s' }),
        },
      }),
    );

    const health = screen.getByTestId('telegram-health');
    expect(health).toHaveTextContent('Source active');
    expect(health).not.toHaveTextContent(/défaut/);
    // La raison reste affichée — c’est du contexte, pas une alerte.
    expect(screen.getByTestId('telegram-health-reason')).toHaveTextContent(
      'rate-limited by Telegram, resuming in 42s',
    );
  });

  it('reste vert avec « no channels configured » : ne polluer personne est un état sain', async () => {
    await mount(
      runtime({
        telegram_channels: [],
        source_status: { telegram: status({ ok: true, reason: 'no channels configured' }) },
      }),
    );

    expect(screen.getByTestId('telegram-health')).toHaveTextContent('Source active');
    expect(screen.getByTestId('telegram-health-reason')).toHaveTextContent(
      'no channels configured',
    );
  });

  it('ne présente pas un relevé périmé comme une lecture vivante', async () => {
    await mount(
      runtime({
        source_status: {
          telegram: status({ ok: true, updated_at: new Date(Date.now() - 3_600_000).toISOString() }),
        },
      }),
    );

    const health = screen.getByTestId('telegram-health');
    expect(health).toHaveTextContent('Lecture figée');
    expect(health).not.toHaveTextContent('Source active');
    expect(screen.getByTestId('telegram-health-age')).toHaveTextContent('il y a 1h');
  });

  it('une plateforme absente de source_status est inconnue, jamais verte', async () => {
    await mount(runtime({ source_status: {} }));

    const health = screen.getByTestId('telegram-health');
    expect(health).toHaveTextContent('État inconnu');
    expect(health).not.toHaveTextContent('Source active');
    expect(screen.queryByTestId('telegram-health-age')).not.toBeInTheDocument();
    expect(screen.queryByTestId('telegram-health-reason')).not.toBeInTheDocument();
  });

  it('signale un canal écarté et rend sa raison atteignable', async () => {
    await mount(
      runtime({
        telegram_channels: ['bwenews', 'typo_channel'],
        source_status: {
          telegram: status({ channels: { typo_channel: 'UsernameNotOccupiedError' } }),
        },
      }),
    );

    const broken = screen.getByLabelText('typo_channel — non lisible : UsernameNotOccupiedError');
    expect(within(broken).getByTestId('ReportProblemIcon')).toBeInTheDocument();
    // Le canal sain n’emprunte pas le même traitement.
    const healthy = screen.getByLabelText('bwenews');
    expect(within(healthy).queryByTestId('ReportProblemIcon')).not.toBeInTheDocument();
  });
});

describe('SourcesPanel — visibilité de l’éditeur', () => {
  it('masque l’éditeur quand la plateforme Telegram est coupée', async () => {
    api.runtime.mockResolvedValue(runtime({ platforms: { telegram: false } }));
    api.aiQuota.mockResolvedValue({ paused: false, resume_at: null, workers: [] });
    render(<SourcesPanel />);

    await screen.findByText('Sources de données');
    await waitFor(() => expect(screen.getByText('Bluesky')).toBeInTheDocument());
    expect(screen.queryByText('Canaux Telegram')).not.toBeInTheDocument();
  });

  it('masque l’éditeur quand toute la catégorie sociale est coupée', async () => {
    api.runtime.mockResolvedValue(runtime({ social_enabled: false }));
    api.aiQuota.mockResolvedValue({ paused: false, resume_at: null, workers: [] });
    render(<SourcesPanel />);

    await screen.findByText('Sources de données');
    await waitFor(() => expect(screen.getByText('Bluesky')).toBeInTheDocument());
    expect(screen.queryByText('Canaux Telegram')).not.toBeInTheDocument();
  });
});
