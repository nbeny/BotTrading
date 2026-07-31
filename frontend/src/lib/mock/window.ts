/**
 * One source of truth for the mock pipeline's per-window volumes.
 *
 * Two reasons this exists rather than each mock hardcoding its own numbers.
 *
 * First, the graph and the Entonnoir sit side by side on the Command Center and
 * count the same things — the backend deliberately reuses the funnel's stage
 * predicates so the two can never disagree. Before this module they disagreed
 * loudly in mock mode: the graph claimed 7735 analyses and 329 escalations while
 * the funnel beside it claimed 1000 and 0. Two panels contradicting each other
 * about the same pipeline is exactly the confusion this whole feature was built
 * to remove, so the fixture must not stage it.
 *
 * Second, mock mode is the repo default and — with no JS test runner — the only
 * place this feature is checked by eye. If the numbers never moved with the
 * window, someone testing the selector could not tell a working control from a
 * dead one.
 *
 * The 24h column is the real production shape (see CLAUDE.md and
 * `memory/pipeline-bottleneck-measured.md`): a pipeline that triages thousands
 * and executes nothing, collapsing at the Sonnet budget.
 */

/** Rough activity multiplier per window, relative to the canonical 24h numbers. */
const WINDOW_SCALE: Record<string, number> = { '1h': 1 / 24, '24h': 1, '7d': 7 };

export function windowScale(window: string): number {
  return WINDOW_SCALE[window] ?? 1;
}

/**
 * Scale a canonical 24h count to `window`.
 *
 * A genuine zero stays zero at every window — "measured, nothing happened" is a
 * fact about the pipeline, not about the window length, and rounding it into
 * existence would undo the one distinction this panel exists to make. A non-zero
 * count never rounds down to 0 for the same reason in reverse: showing "aucun
 * élément" for an hour that really did see two items would be its own small lie.
 */
export function scaleCount(count: number, window: string): number {
  if (count === 0) return 0;
  return Math.max(1, Math.round(count * windowScale(window)));
}

/** Canonical 24h volumes, keyed by the graph's stage ids. */
export const STAGE_VOLUME_24H = {
  collect: 8420,
  sentiment: 6180,
  triage: 7735,
  senior: 329,
  decision: 12,
  risk: 4,
  execute: 0,
} as const;

/** Canonical 24h drop counts. `collect` has no drop notion of its own. */
export const STAGE_DROPPED_24H: Record<string, number | null> = {
  collect: null,
  sentiment: 240,
  triage: 7406,
  senior: 328,
  decision: 317,
  risk: 8,
  execute: 0,
};

/**
 * The funnel's five stages map onto the graph's, so both panels can be derived
 * from the same numbers instead of drifting apart.
 */
export const FUNNEL_FROM_STAGE = {
  analyses: 'triage',
  escalated: 'senior',
  decisions: 'decision',
  approved: 'risk',
  executed: 'execute',
} as const;
