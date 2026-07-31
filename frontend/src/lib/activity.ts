/**
 * How busy a pipeline stage looks, as a colour.
 *
 * The graph used to colour its nodes by service health, which answered "is this
 * process up?" — a question the health rail below already answers. What an
 * operator actually wants from the graph is *where the work is happening right
 * now*, so the node body now encodes activity and the health dot keeps
 * reporting health.
 *
 * Intensity is relative to the busiest stage in the same snapshot: the point is
 * to compare stages against each other, not against an absolute scale that
 * would need re-tuning every time the pipeline's throughput changes.
 */

/** Nothing measured, or measured and genuinely nothing. */
const IDLE_RGB = { r: 91, g: 101, b: 121 };
export const IDLE_COLOR = `rgb(${IDLE_RGB.r}, ${IDLE_RGB.g}, ${IDLE_RGB.b})`;

/**
 * A non-zero stage never falls below this share of the ramp.
 *
 * Volumes here span three orders of magnitude — 7735 analyses against 12
 * decisions — so a pure linear share would render the decision stage at 0.2% and
 * paint it the same grey as a dead one. Grey has to keep meaning "nothing", so
 * anything that moved at all stays visibly green.
 */
const FLOOR = 0.28;

/** Dimmest and brightest ends of the green ramp. */
const DIM = { r: 24, g: 74, b: 55 };
const BRIGHT = { r: 38, g: 208, b: 124 };

/**
 * Relative activity of one stage, 0..1.
 *
 * `null` (not measured) and `0` (measured, nothing happened) both return 0 —
 * they are different facts, but neither is activity, and the node already
 * spells the difference out in words underneath.
 */
export function activityRatio(volume: number | null, busiest: number): number {
  if (!volume || busiest <= 0) return 0;
  // Log-compressed, because these volumes span three orders of magnitude: on a
  // linear share, 7735 analyses flatten 12 decisions and 4 approvals into the
  // same colour, and the bottom of the pipeline — where the bottleneck actually
  // is — becomes unreadable. Measured on real data before choosing: linear gave
  // decision and risk the identical rgb(28,112,74).
  const share = Math.min(1, Math.log10(1 + volume) / Math.log10(1 + busiest));
  return FLOOR + share * (1 - FLOOR);
}

/** The busiest stage's volume, used as the denominator for the whole row. */
export function busiestVolume(stages: { volume: number | null }[]): number {
  return stages.reduce((max, s) => Math.max(max, s.volume ?? 0), 0);
}

/**
 * Green for a stage, grey when nothing crossed it.
 *
 * `pulse` (0..1) is the live overlay: a WebSocket event just arrived for this
 * stage, so it briefly brightens past its steady-state colour. It decays back
 * on its own, which is what makes a busy stage visibly flicker while a quiet
 * one sits still.
 */
export function activityRgb(ratio: number, pulse = 0): { r: number; g: number; b: number } {
  if (ratio <= 0) return IDLE_RGB;
  const t = Math.min(1, ratio + pulse * (1 - ratio));
  const mix = (a: number, b: number) => Math.round(a + (b - a) * t);
  return { r: mix(DIM.r, BRIGHT.r), g: mix(DIM.g, BRIGHT.g), b: mix(DIM.b, BRIGHT.b) };
}

/**
 * The same colour at a given opacity.
 *
 * Returned as `rgba()` rather than a hex string with an alpha suffix, because
 * the ramp is interpolated numerically and `#rrggbb` + `1a` only works for
 * colours that were literals to begin with.
 */
export function rgba({ r, g, b }: { r: number; g: number; b: number }, alpha: number): string {
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function activityColor(ratio: number, pulse = 0): string {
  const { r, g, b } = activityRgb(ratio, pulse);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * Which stage a broadcast event belongs to.
 *
 * Two known gaps, both deliberate rather than oversights:
 * `senior` (Sonnet) publishes a `DecisionEvent` exactly like the decision
 * engine does, so the two cannot be told apart on the wire; and the social/news
 * topics are orphaned — that ingestion path writes to `raw_content` instead of
 * Kafka. Those stages therefore get no live pulse. They are still coloured by
 * their counted volume, which is why the base colour comes from the database
 * and not from this stream: a stage must never look dead merely because its
 * work is invisible to the WebSocket.
 */
export const STAGE_BY_EVENT: Record<string, string> = {
  PriceEvent: 'collect',
  VolumeEvent: 'collect',
  DexEvent: 'collect',
  NewsEvent: 'collect',
  SocialEvent: 'collect',
  SentimentEvent: 'sentiment',
  AnalysisEvent: 'triage',
  DecisionEvent: 'decision',
  RiskApprovedEvent: 'risk',
  OrderExecutedEvent: 'execute',
};
