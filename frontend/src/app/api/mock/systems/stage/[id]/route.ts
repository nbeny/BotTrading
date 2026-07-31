/**
 * Mock for `GET /systems/stage/{id}` — the drawer behind one node of the
 * pipeline graph. Volume/dropped are read straight off `getSystemsSnapshot()`
 * so the drawer never disagrees with the graph it was opened from within the
 * same page load (they can drift across separate polls, same as the graph
 * itself re-jitters — that's the mock's intentional "live" feel, not a bug).
 *
 * An unknown stage id 404s, matching `services/api-gateway/app/read_api.py`'s
 * `systems_stage`: an empty payload would read as "this stage processed
 * nothing", which is a different, false statement.
 */
import { NextResponse } from 'next/server';
import { getSystemsSnapshot } from '@/lib/mock/systems';
import { pick, rand, round, uid } from '@/lib/mock/universe';
import type { StageDetail, StageItem } from '@/lib/types/systems';

type ItemBuilder = (i: number, at: string) => StageItem;

// Roughly a third of items carry a correlation_id — only for stages whose
// backend rows actually have one (Signal/DecisionJournal/Decision/Trade).
// `collect` (raw_content) and `sentiment` (scored raw_content) never do, in
// mock or in production, so forcing one on here would open a trace drawer
// with nothing behind it.
const withCorrelation = (i: number) => (i % 3 === 0 ? uid('cid') : null);

const BUILDERS: Record<string, ItemBuilder> = {
  collect: (i, at) => {
    const source = pick(['reddit', 'bluesky', 'mastodon', 'cryptocompare', 'rss', 'gdelt']);
    const kind = source === 'cryptocompare' || source === 'rss' || source === 'gdelt' ? 'article' : 'post';
    return {
      at, symbol: pick(['BTC', 'ETH', 'SOL', null]),
      summary: `${source} · ${kind}`,
      detail: { source, kind, url: `https://example.com/${source}/${i}` },
      correlation_id: null,
    };
  },
  sentiment: (i, at) => {
    const score = round(rand(-0.6, 0.7), 2);
    return {
      at, symbol: pick(['BTC', 'ETH', 'SOL', 'ARB']),
      summary: `bluesky · sentiment ${score >= 0 ? '+' : ''}${score.toFixed(2)}`,
      detail: { score, model: 'cryptobert' },
      correlation_id: null,
    };
  },
  triage: (i, at) => {
    const escalated = i % 6 === 0;
    const score = Math.round(rand(escalated ? 65 : 10, escalated ? 90 : 60));
    const blockReason = escalated ? null : 'score_below_threshold';
    return {
      at, symbol: pick(['SOL', 'BTC', 'ETH', 'AVAX']),
      summary: `score ${score} · ${escalated ? 'escaladé' : blockReason}`,
      detail: { score, confidence: round(rand(0.4, 0.9), 2), factors_present: Math.round(rand(1, 4)), block_reason: blockReason },
      correlation_id: withCorrelation(i),
    };
  },
  senior: (i, at) => {
    const direction = pick(['long', 'short']);
    const score = Math.round(rand(60, 92));
    return {
      at, symbol: 'SOL',
      summary: `Sonnet ${direction} · score ${score}`,
      detail: { score, sonnet_score: score, sonnet_validated: score >= 70, skip_reason: null },
      correlation_id: withCorrelation(i),
    };
  },
  decision: (i, at) => {
    const direction = pick(['long', 'short']);
    const confidence = round(rand(0.55, 0.9), 2);
    return {
      at, symbol: 'SOL',
      summary: `${direction} · confiance ${Math.round(confidence * 100)}%`,
      detail: { direction, score: Math.round(rand(60, 90)), ai_validated: true },
      correlation_id: withCorrelation(i),
    };
  },
  risk: (i, at) => {
    const direction = pick(['long', 'short']);
    const sizePct = round(rand(2, 6), 1);
    return {
      at, symbol: 'SOL',
      summary: `${direction} · taille ${sizePct}%`,
      detail: {
        size_pct: sizePct,
        stop_loss: round(rand(0.02, 0.05), 3),
        take_profit: round(rand(0.04, 0.1), 3),
        rr: round(rand(1.5, 3), 1),
      },
      correlation_id: withCorrelation(i),
    };
  },
  // Never actually invoked — `itemCount` below is forced to 0 for 'execute'
  // (trading-engine executed nothing this window). But the lookup that
  // guards the 404 needs a key present here, or the drawer's real empty
  // state becomes unreachable and shows as "unknown stage" instead.
  execute: (i, at) => ({
    at, symbol: 'SOL',
    summary: 'ordre exécuté',
    detail: {},
    correlation_id: null,
  }),
};

// Breakdown per stage. `sentiment` mirrors the backend's fixed two-bucket
// shape exactly (scoré vs. en attente); the others group by rejection reason,
// summed to add up to that stage's own `dropped` — a breakdown that doesn't
// reconcile with the headline number it explains is worse than none.
function breakdown(id: string, volume: number, dropped: number): { key: string; count: number }[] {
  switch (id) {
    case 'collect':
      return [
        { key: 'reddit', count: Math.round(volume * 0.25) },
        { key: 'bluesky', count: Math.round(volume * 0.22) },
        { key: 'rss', count: Math.round(volume * 0.14) },
        { key: 'prices', count: volume - Math.round(volume * 0.61) },
      ];
    case 'sentiment':
      return [
        { key: 'scoré', count: volume },
        { key: 'en attente', count: dropped },
      ];
    case 'triage':
      return [
        { key: 'score_below_threshold', count: Math.round(dropped * 0.97) },
        { key: 'gate_not_met', count: dropped - Math.round(dropped * 0.97) },
      ];
    case 'senior':
      return [
        { key: 'budget_exceeded', count: Math.round(dropped * 0.91) },
        { key: 'rate_limited', count: dropped - Math.round(dropped * 0.91) },
      ];
    case 'decision':
      return [
        { key: 'low_confidence', count: Math.round(dropped * 0.66) },
        { key: 'conflicting_signals', count: dropped - Math.round(dropped * 0.66) },
      ];
    case 'risk':
      return [
        { key: 'position_size_exceeded', count: Math.round(dropped * 0.625) },
        { key: 'leverage_exceeded', count: dropped - Math.round(dropped * 0.625) },
      ];
    case 'execute':
      // 0 submitted, 0 failed this window — nothing to group by status.
      return [];
    default:
      return [];
  }
}

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { searchParams } = new URL(req.url);
  const window = searchParams.get('window') ?? '24h';
  const limit = Math.max(1, Math.min(50, Number(searchParams.get('limit') ?? 20)));

  const stage = getSystemsSnapshot().pipeline.find((p) => p.id === id);
  const builder = BUILDERS[id];
  if (!stage || !builder) {
    return NextResponse.json({ detail: `unknown stage '${id}'` }, { status: 404 });
  }

  const volume = stage.volume ?? 0;
  const dropped = stage.dropped ?? 0;
  // `execute` genuinely produced nothing this window — the drawer's own empty
  // state (not a spinner, not a fabricated row) has to be reachable in mock.
  const itemCount = id === 'execute' ? 0 : Math.min(limit, volume || limit);
  const items: StageItem[] = Array.from({ length: itemCount }, (_, i) =>
    builder(i, new Date(Date.now() - (i + 1) * 90_000).toISOString()),
  );

  const detail: StageDetail = {
    id: stage.id,
    label: stage.label,
    window,
    volume: stage.volume,
    dropped: stage.dropped,
    breakdown: breakdown(id, volume, dropped),
    items,
    updated_at: new Date().toISOString(),
  };
  return NextResponse.json(detail);
}
