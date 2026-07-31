/**
 * Mock generator for `GET /systems/funnel`. This deliberately mirrors the
 * *actual* production situation described in CLAUDE.md — the pipeline
 * currently produces zero decisions — rather than a healthy fiction: almost
 * everything dies at the Haiku triage stage (`score_below_threshold`), and
 * most of the analyses that do get built only have 2 of the 4 scoring
 * factors available (sentiment + one on-chain signal are usually missing).
 * The panel exists to diagnose exactly this shape, so the mock has to look
 * like it, not like a demo.
 */
import type { FunnelStats } from '@/lib/types/systems';
import { FUNNEL_FROM_STAGE, STAGE_VOLUME_24H, scaleCount } from './window';

function funnelStages(window: string) {
  const order = Object.entries(FUNNEL_FROM_STAGE) as [
    keyof typeof FUNNEL_FROM_STAGE,
    keyof typeof STAGE_VOLUME_24H,
  ][];
  let previous: number | null = null;
  return order.map(([stage, sourceStage]) => {
    const count = scaleCount(STAGE_VOLUME_24H[sourceStage], window);
    // The first stage converts from itself, matching funnel.build_funnel; a
    // zero upstream reports 0.0 rather than dividing.
    const conversion_pct = previous === null
      ? (count ? 100.0 : 0.0)
      : previous > 0
        ? Math.round((count / previous) * 1000) / 10
        : 0.0;
    previous = count;
    return { stage, count, conversion_pct };
  });
}

export function getFunnelStats(window = '24h'): FunnelStats {
  return {
    window,
    // Derived from the same canonical numbers as the graph's stages. These two
    // panels sit side by side and the backend counts them with identical
    // predicates, so a fixture that let them disagree would stage exactly the
    // contradiction this feature exists to remove.
    stages: funnelStages(window),
    // Scores skew low — almost everything sits well under the escalation
    // threshold, which is exactly why nothing escalates.
    score_histogram: [
      { bucket: 0, count: 400 },
      { bucket: 10, count: 500 },
      { bucket: 20, count: 62 },
      { bucket: 30, count: 20 },
      { bucket: 40, count: 10 },
      { bucket: 50, count: 5 },
      { bucket: 60, count: 2 },
      { bucket: 70, count: 1 },
      { bucket: 80, count: 0 },
      { bucket: 90, count: 0 },
    ],
    // Most analyses (900/1000) are built on only 2 of the 4 scoring
    // factors — a score of "35" built on 2 factors is not the same signal
    // as a "35" built on 4, which is part of why the threshold looks wrong.
    factors_presence: { '0': 0, '1': 12, '2': 900, '3': 88, '4': 0 },
    top_block_reasons: [
      { stage: 'haiku', reason: 'score_below_threshold', count: 980 },
      { stage: 'haiku', reason: 'gate_not_met', count: 20 },
      // Numbers in a reason are normalised to N server-side, so that one row
      // stands for every score that fell short rather than one row per score.
      { stage: 'decision_engine', reason: 'score N below decision threshold N', count: 143 },
    ],
    block_reasons_truncated: false,
    updated_at: new Date().toISOString(),
  };
}
