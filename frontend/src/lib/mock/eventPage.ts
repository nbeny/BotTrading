/**
 * Mock generator for `GET /events` — the server-side event archive.
 *
 * This implements *real* keyset pagination rather than always returning the
 * same page: the client's `before` cursor is honoured, so infinite scroll is
 * exercisable with `NEXT_PUBLIC_USE_MOCK=1` and no Python running. A mock that
 * ignored the cursor would leave the pagination path untested until deploy.
 *
 * Cursor format matches the backend exactly (`<time>|<event_id>`, where `time`
 * has the shape of Python's `datetime.isoformat()` — 6 fractional digits and an
 * explicit `+00:00` offset). The client treats it as opaque and round-trips it,
 * so the only thing that matters is that both ends agree on the shape.
 */
import { pick, rand, reason, round, uid, UNIVERSE } from './universe';
import type { ArchivedEvent, EventPage } from '@/lib/types/events';

/** ~4h of archived history, so a limit=100 client gets several pages. */
const POOL_SIZE = 240;
const SPACING_MS = 60_000;
const MAX_LIMIT = 200;

/** Python `datetime.isoformat()` shape: microsecond precision, explicit offset. */
function pyIso(atMs: number): string {
  return `${new Date(atMs).toISOString().slice(0, -1)}000+00:00`;
}

const KINDS = ['price', 'price', 'price', 'sentiment', 'analysis', 'decision'] as const;
type Kind = (typeof KINDS)[number];

const TOPIC: Record<Kind, string> = {
  price: 'market.price.events',
  sentiment: 'market.sentiment.events',
  analysis: 'market.analysis.events',
  decision: 'decision.events',
};
const TYPE: Record<Kind, string> = {
  price: 'PriceEvent',
  sentiment: 'SentimentEvent',
  analysis: 'AnalysisEvent',
  decision: 'DecisionEvent',
};
const SOURCE: Record<Kind, string> = {
  price: 'coingecko',
  sentiment: 'sentiment-service',
  analysis: 'ai-worker-haiku',
  decision: 'ai-worker-sonnet',
};

function bodyFor(kind: Kind, price: number): Record<string, unknown> {
  switch (kind) {
    case 'price':
      return {
        price_usd: String(price),
        volume_24h_usd: String(Math.round(rand(2e7, 4e9))),
        price_change_pct_24h: round(rand(-9, 12), 2),
        is_trending: Math.random() > 0.7,
      };
    case 'sentiment':
      return {
        sentiment_score: round(rand(-0.8, 0.9), 2),
        confidence: round(rand(0.6, 0.98), 2),
        model_name: 'ElKulako/cryptobert',
        input_kind: pick(['social', 'news']),
        sample_size: Math.floor(rand(10, 120)),
      };
    case 'analysis': {
      const score = Math.floor(rand(20, 95));
      return {
        opportunity_score: score,
        confidence: round(rand(0.55, 0.95), 2),
        reason: reason(),
        price_change_pct_24h: round(rand(-6, 14), 1),
        volume_spike_ratio: round(rand(1, 5), 1),
        escalate: score >= 78,
      };
    }
    case 'decision':
      return {
        direction: Math.random() > 0.25 ? 'long' : 'short',
        opportunity_score: Math.floor(rand(60, 92)),
        confidence: round(rand(0.6, 0.93), 2),
        rationale:
          'Momentum soutenu, liquidité saine et sentiment corroborant. Entrée justifiée par le ratio risque/rendement.',
        key_risks: ['volatilité macro', 'corrélation BTC élevée'],
        ai_validated: Math.random() > 0.3,
      };
  }
}

function makeArchived(atMs: number): ArchivedEvent {
  const kind = pick(KINDS as unknown as Kind[]);
  const token = pick(UNIVERSE);
  const time = pyIso(atMs);
  const eventId = uid('evt');
  const corr = uid('corr');
  return {
    time,
    event_id: eventId,
    event_type: TYPE[kind],
    topic: TOPIC[kind],
    symbol: token.symbol,
    correlation_id: corr,
    payload: {
      event_id: eventId,
      event_type: TYPE[kind],
      schema_version: 1,
      occurred_at: time,
      source: SOURCE[kind],
      correlation_id: corr,
      symbol: token.symbol,
      ...bodyFor(kind, round(token.price * (1 + rand(-0.03, 0.03)), 4)),
    },
  };
}

// Built once per server process and then frozen: cursors must stay valid across
// requests, so the archive cannot be re-randomised on every call (unlike the
// live simulation in `sim.ts`, which is deliberately time-evolving).
let _pool: ArchivedEvent[] | null = null;
function pool(): ArchivedEvent[] {
  if (!_pool) {
    const now = Date.now();
    _pool = Array.from({ length: POOL_SIZE }, (_, i) => makeArchived(now - i * SPACING_MS));
  }
  return _pool;
}

export interface EventPageQuery {
  limit?: number;
  before?: string | null;
  types?: string | null;
  symbol?: string | null;
}

export function getEventPage(q: EventPageQuery = {}): EventPage {
  const limit = Math.min(Math.max(q.limit ?? 100, 1), MAX_LIMIT);
  let items = pool();

  if (q.types) {
    const wanted = new Set(
      q.types
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    );
    if (wanted.size) items = items.filter((e) => wanted.has(e.event_type));
  }
  if (q.symbol) {
    const sym = q.symbol.toUpperCase();
    items = items.filter((e) => e.symbol === sym);
  }

  // Keyset pagination: strictly older than the cursor, ties broken on event_id.
  if (q.before) {
    const sep = q.before.lastIndexOf('|');
    const time = sep === -1 ? q.before : q.before.slice(0, sep);
    const id = sep === -1 ? '' : q.before.slice(sep + 1);
    items = items.filter((e) => e.time < time || (e.time === time && e.event_id < id));
  }

  const page = items.slice(0, limit);
  const last = page[page.length - 1];
  const next_cursor = last && items.length > page.length ? `${last.time}|${last.event_id}` : null;
  return { items: page, next_cursor };
}
