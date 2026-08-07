import type {
  ThresholdAxis,
  ThresholdReportPayload,
  ThresholdReportResponse,
  ThresholdWarning,
} from '@/lib/types/threshold';

// Deterministic mock — no Math.random, tests consume this. Exercises the
// *refusal* case on purpose: it's the one the UI must render well. Mirrors
// the real 2026-08-04 incident (services/decision-engine/app/threshold_scan.py
// docstring): `positioning` reporting 1 line out of 1 281 511, `fundamentals`
// legitimately rare at ~3.6%, and a day-volume spike/gap in the same window
// (671 955 on 07-31, 0 the next day) — the exact numbers cited in the module.

const TOTAL = 1_281_511;

/** Axis order and weights mirror `scoring.py::WEIGHTS`, sorted desc — the
 *  same order `threshold_scan.py::analyze` produces. */
const AXIS_SEEN: Record<string, { weight: number; seen: number }> = {
  volume_growth: { weight: 0.1725, seen: 1_245_000 },
  social_score: { weight: 0.138, seen: 980_000 },
  news_score: { weight: 0.138, seen: 1_100_251 },
  market_trend: { weight: 0.138, seen: 1_270_884 },
  // The 2026-08-04 case: a collector restart that survived one cycle before
  // failing again. 1 / 1 281 511 rounds to "0.0%" at one decimal but is not
  // *equal* to 0.0 — the raw count must travel with the percentage.
  positioning: { weight: 0.138, seen: 1 },
  liquidity_score: { weight: 0.1035, seen: 1_281_000 },
  // Legitimately rare, not broken: cited in the module as the one measured
  // rate for an axis that works (~3.6%).
  fundamentals: { weight: 0.092, seen: 46_134 },
  developer_activity: { weight: 0.08, seen: 15_400 },
};

const MIN_PRESENCE_PCT = 1.0;

function axes(): ThresholdAxis[] {
  return Object.entries(AXIS_SEEN).map(([key, { weight, seen }]) => {
    const pct = (100 * seen) / TOTAL;
    return { key, weight, seen, pct, mute: pct < MIN_PRESENCE_PCT };
  });
}

const BY_DAY: Record<string, number> = {
  '2026-07-29': 155_000,
  '2026-07-30': 148_000,
  '2026-07-31': 671_955, // measured spike (module docstring)
  '2026-08-01': 0, // measured empty day, same docstring
  '2026-08-02': 92_000,
  '2026-08-03': 64_556,
  '2026-08-04': 150_000,
};

const WARNINGS: ThresholdWarning[] = [
  { code: 'EMPTY_DAY', day: '2026-08-01', text: '2026-08-01 : aucune ligne (jour vide)' },
  {
    code: 'DEVIATION',
    day: '2026-07-31',
    text: '2026-07-31 : 671955 lignes, ecart fort a la mediane de la fenetre (148000)',
  },
  {
    code: 'DEVIATION',
    day: '2026-08-03',
    text: '2026-08-03 : 64556 lignes, ecart fort a la mediane de la fenetre (148000)',
  },
];

// Copied verbatim from threshold_scan.py::analyze's MUTE_AXES refusal —
// the paragraph that distinguishes "collecteur casse" from "axe legitimement
// rare" is the value of the refusal, it must not be paraphrased.
const MUTE_AXES_DETAIL =
  "Un axe absent est EXCLU du denominateur de renormalisation, pas note " +
  'zero -- donc un seuil calibre ici vaudrait pour un modele ampute de ' +
  "ce poids, et deviendrait faux des que l'axe reparle, sans erreur ni " +
  'test rouge.\n\n' +
  'Ce refus a deux lectures possibles, et le pourcentage seul ne ' +
  'tranche pas entre elles :\n' +
  '  1. la collecte est cassee -- un collecteur echoue en silence ' +
  '(ex. collector-binance-futures le 2026-08-04) ; verifie ses logs et ' +
  "son /health, qui repond 503 apres plusieurs echecs consecutifs.\n" +
  "  2. l'axe est legitimement rare -- il ne s'applique qu'a un " +
  'sous-ensemble de tokens (positioning: pas de contrat perpetuel ; ' +
  "developer_activity: pas de depot public), et sa couverture reelle " +
  'est simplement basse. Repere mesure : fundamentals tourne a 3,6% de ' +
  'presence et fonctionne.\n' +
  'Ce qui distingue les deux : rapporte le compte brut affiche ' +
  "ci-dessus au nombre de tokens de la fenetre auxquels l'axe peut " +
  "s'appliquer (pas au total des lignes, qui inclut les tokens hors " +
  "champ). Si tu conclus a la seconde lecture, ajuste " +
  'MIN_PRESENCE_PCT en connaissance de cause -- ne contourne pas ce ' +
  'garde. MIN_PRESENCE_PCT reste un plancher provisoire, uniforme sur ' +
  "les huit axes en l'absence de planchers mesures par axe (cf. le " +
  "commentaire de la constante).";

export function getThresholdReportPayload(): ThresholdReportPayload {
  return {
    window: {
      days: 7,
      min_time: '2026-07-29T00:00:00+00:00',
      total: TOTAL,
      no_evidence: 18_204,
      by_day: BY_DAY,
    },
    axes: axes(),
    refusal: {
      code: 'MUTE_AXES',
      title: `positioning sous ${MIN_PRESENCE_PCT.toFixed(1)}% de presence sur la fenetre.`,
      detail: MUTE_AXES_DETAIL,
    },
    // A refusal short-circuits before distribution/sonnet are computed —
    // stable-shaped, all-null, never `{}` (cf. `_empty_distribution`/`_empty_sonnet`).
    distribution: { scored: null, p50: null, p90: null, p99: null, p999: null, max: null },
    proposal: null,
    warnings: WARNINGS,
    sonnet: { n: null, p50: null, max: null, warning: null },
    risk_min_confidence: 0.3795,
    min_presence_pct: MIN_PRESENCE_PCT,
  };
}

export function getThresholdReport(): ThresholdReportResponse {
  return {
    report: getThresholdReportPayload(),
    status: 'ok',
    error: null,
    computed_at: '2026-08-04T14:32:07+00:00',
    window_days: 7,
    target_per_day: 200,
    duration_s: 42.7,
    running: false,
  };
}
