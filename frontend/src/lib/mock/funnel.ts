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

export function getFunnelStats(window = '24h'): FunnelStats {
  return {
    window,
    // Stalled at the very first gate: Haiku triage almost never escalates,
    // so every stage downstream of it is starved to zero. This is the
    // production reality, not a bug in the mock.
    stages: [
      { stage: 'analyses', count: 1000, conversion_pct: 100.0 },
      { stage: 'escalated', count: 0, conversion_pct: 0.0 },
      { stage: 'decisions', count: 0, conversion_pct: 0.0 },
      { stage: 'approved', count: 0, conversion_pct: 0.0 },
      { stage: 'executed', count: 0, conversion_pct: 0.0 },
    ],
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
