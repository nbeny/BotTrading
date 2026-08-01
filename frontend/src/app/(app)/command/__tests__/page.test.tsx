import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AnalysisEvent, DecisionEvent, PriceEvent, SentimentEvent } from '@/lib/types/events';

// The Command Center mounts a dozen widgets, each fetching and/or subscribing
// to the WS bus on their own. None of that is relevant to "is SignalsTable
// wired up correctly" — mocking them out to inert stubs keeps the test
// focused on the page's own wiring instead of re-testing every sibling.
vi.mock('@/components/command/KpiTicker', () => ({ KpiTicker: () => null }));
vi.mock('@/components/systems/PipelineFlow', () => ({ PipelineFlow: () => null }));
vi.mock('@/components/command/LiveEventStream', () => ({ LiveEventStream: () => null }));
vi.mock('@/components/command/DecisionTraceDrawer', () => ({ DecisionTraceDrawer: () => null }));
vi.mock('@/components/command/StageDetailDrawer', () => ({ StageDetailDrawer: () => null }));
vi.mock('@/components/command/WindowSelector', () => ({ WindowSelector: () => null }));
vi.mock('@/components/command/AiDecisionFeed', () => ({ AiDecisionFeed: () => null }));
vi.mock('@/components/command/FunnelPanel', () => ({ FunnelPanel: () => null }));
vi.mock('@/components/command/LivePnlPanel', () => ({ LivePnlPanel: () => null }));
vi.mock('@/components/command/MarketHeatPanel', () => ({ MarketHeatPanel: () => null }));
vi.mock('@/components/command/GuardrailPanel', () => ({ GuardrailPanel: () => null }));
vi.mock('@/components/command/HealthRail', () => ({ HealthRail: () => null }));

// `useEventsPerMin` (defined in page.tsx itself) subscribes directly, so the
// hook must be mocked even though every widget that also uses it is stubbed
// above.
vi.mock('@/lib/ws/WebSocketProvider', () => ({ useEventSubscription: vi.fn() }));

vi.mock('@/lib/api/endpoints', () => ({
  portfolioApi: { get: vi.fn() },
  tradingApi: { status: vi.fn() },
  riskApi: { exposure: vi.fn() },
  systemsApi: { overview: vi.fn() },
  marketApi: { signals: vi.fn() },
}));

import { portfolioApi, tradingApi, riskApi, systemsApi, marketApi } from '@/lib/api/endpoints';
import CommandCenterPage from '../page';

const signalsMock = vi.mocked(marketApi.signals);
const portfolioMock = vi.mocked(portfolioApi.get);
const statusMock = vi.mocked(tradingApi.status);
const exposureMock = vi.mocked(riskApi.exposure);
const overviewMock = vi.mocked(systemsApi.overview);

function makeAnalysisEvent(overrides: Partial<AnalysisEvent> = {}): AnalysisEvent {
  return {
    event_id: 'evt-1',
    event_type: 'AnalysisEvent',
    source: 'ai-worker-haiku',
    occurred_at: '2026-08-01T09:00:00Z',
    symbol: 'SOL',
    opportunity_score: 0.79,
    confidence: 0.8,
    reason: 'Momentum haussier confirmé',
    escalate: false,
    ...overrides,
  };
}

function makeDecisionEvent(overrides: Partial<DecisionEvent> = {}): DecisionEvent {
  return {
    event_id: 'evt-2',
    event_type: 'DecisionEvent',
    source: 'decision-engine',
    occurred_at: '2026-08-01T09:01:00Z',
    symbol: 'ETH',
    direction: 'long',
    opportunity_score: 0.62,
    confidence: 0.7,
    rationale: 'Setup validé par Sonnet',
    key_risks: [],
    ai_validated: true,
    ...overrides,
  };
}

// Unused by these tests directly, but documents the full union `marketApi.signals`
// can resolve — kept here so a future test extending this file has fixtures for
// the other two variants at hand.
function makePriceEvent(overrides: Partial<PriceEvent> = {}): PriceEvent {
  return {
    event_type: 'PriceEvent',
    source: 'collector-coingecko',
    occurred_at: '2026-08-01T09:02:00Z',
    symbol: 'BTC',
    price_usd: '65000',
    price_change_pct_24h: 1.2,
    ...overrides,
  };
}
void makePriceEvent;
function makeSentimentEvent(overrides: Partial<SentimentEvent> = {}): SentimentEvent {
  return {
    event_type: 'SentimentEvent',
    source: 'sentiment-service',
    occurred_at: '2026-08-01T09:03:00Z',
    symbol: 'SOL',
    sentiment_score: 0.3,
    confidence: 0.5,
    model_name: 'cryptobert',
    input_kind: 'social',
    sample_size: 10,
    ...overrides,
  };
}
void makeSentimentEvent;

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CommandCenterPage />
    </QueryClientProvider>,
  );
}

/** The page's other four queries are irrelevant to SignalsTable wiring (their
 *  widgets are stubbed above) but TanStack Query logs a warning if a query fn
 *  resolves `undefined` — `null` keeps the run's output clean. */
function mockUnrelatedQueries() {
  portfolioMock.mockResolvedValue(null as never);
  statusMock.mockResolvedValue(null as never);
  exposureMock.mockResolvedValue(null as never);
  overviewMock.mockResolvedValue(null as never);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('CommandCenterPage — SignalsTable wiring', () => {
  it('rend le tableau des signaux sur le tableau de bord', async () => {
    mockUnrelatedQueries();
    signalsMock.mockResolvedValue([makeAnalysisEvent()]);

    renderPage();

    expect(await screen.findByText('Signaux & Opportunités')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('SOL')).toBeInTheDocument());
  });

  it("affiche un score AnalysisEvent de 0.79 comme 79 via ScoreChip, pas 1 ni 7900", async () => {
    mockUnrelatedQueries();
    signalsMock.mockResolvedValue([makeAnalysisEvent({ opportunity_score: 0.79 })]);

    renderPage();

    await waitFor(() => expect(screen.getByText('79')).toBeInTheDocument());
    expect(screen.queryByText('1')).not.toBeInTheDocument();
    expect(screen.queryByText('7900')).not.toBeInTheDocument();
  });

  it('un DecisionEvent affiche aussi son score à la bonne échelle', async () => {
    mockUnrelatedQueries();
    signalsMock.mockResolvedValue([makeDecisionEvent({ opportunity_score: 0.62 })]);

    renderPage();

    await waitFor(() => expect(screen.getByText('62')).toBeInTheDocument());
  });

  it('sans signal, affiche un état vide plutôt qu’un tableau nu', async () => {
    mockUnrelatedQueries();
    signalsMock.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('Aucun signal disponible pour le moment.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
