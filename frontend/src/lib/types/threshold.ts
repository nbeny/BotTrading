/** Contrat GET /systems/journal/threshold — miroir exact de
 *  decision-engine/app/threshold_scan.py::ThresholdReport.to_payload() et de
 *  l'enveloppe rendue par api-gateway/app/journal_api.py::journal_threshold.
 *  Règle du projet : null = non mesuré = rendu « — », jamais 0. */

export interface ThresholdAxis {
  key: string;
  weight: number;
  seen: number;
  pct: number;
  mute: boolean;
}

/** Le module ne produit que ces trois codes de refus — pas de `NO_DATA` : une
 *  fenêtre vide fait chuter tous les axes sous MIN_PRESENCE_PCT (0/0 → 0.0%
 *  < 1.0%) et retombe donc sur `MUTE_AXES`. */
export type ThresholdRefusalCode = 'MUTE_AXES' | 'NO_REGIME' | 'REGIME_GAP';

export interface ThresholdRefusal {
  code: ThresholdRefusalCode;
  title: string;
  /** Le paragraphe qui distingue « collecte cassée » d'« axe légitimement
   *  rare ». C'est la valeur du refus : il se rend en entier, jamais résumé. */
  detail: string;
  /** Présent seulement pour REGIME_GAP. */
  suggested_days?: number;
}

/** Toujours renvoyé avec le même jeu de clés (`_empty_distribution`) — un
 *  refus rend cet objet avec toutes les valeurs à `null`, jamais `{}`. */
export interface ThresholdDistribution {
  scored: number | null;
  p50: number | null;
  p90: number | null;
  p99: number | null;
  p999: number | null;
  max: number | null;
}

/** Idem, forme stable (`_empty_sonnet`). `n: null` = jamais mesuré, distinct
 *  de `n: 0` = mesuré et vide. */
export interface ThresholdSonnet {
  n: number | null;
  p50: number | null;
  max: number | null;
  warning: string | null;
}

export interface ThresholdRiskMinScoreEffect {
  value: number;
  lines: number;
  pct: number;
  confidence_passing: number;
  is_default: boolean;
}

export interface ThresholdProposal {
  threshold: number;
  target_per_day: number;
  actual_per_day: number;
  distinct_symbols: number;
  passing_pct: number;
  confidence_passing_per_day: number;
  risk_min_score_effect: ThresholdRiskMinScoreEffect[];
}

export interface ThresholdWarning {
  code: 'EMPTY_DAY' | 'DEVIATION';
  day: string;
  text: string;
}

export interface ThresholdWindow {
  days: number;
  min_time: string | null;
  total: number;
  no_evidence: number;
  by_day: Record<string, number>;
}

export interface ThresholdReportPayload {
  window: ThresholdWindow;
  axes: ThresholdAxis[];
  refusal: ThresholdRefusal | null;
  distribution: ThresholdDistribution;
  proposal: ThresholdProposal | null;
  warnings: ThresholdWarning[];
  sonnet: ThresholdSonnet;
  /** Planchers qui ont produit le verdict — voyagent avec le rapport plutôt
   *  que d'être relus depuis les constantes du module. */
  risk_min_confidence: number;
  min_presence_pct: number;
}

/** Enveloppe de GET /systems/journal/threshold (journal_api.py::journal_threshold).
 *  `report`/`report_computed_at` portent le dernier scan RÉUSSI ; `status`/
 *  `error`/`computed_at` portent la tentative la plus RÉCENTE — sur un scan
 *  en échec les deux sont peuplés en même temps : il faut afficher l'erreur
 *  SANS faire disparaître le dernier rapport valide. */
export interface ThresholdReportResponse {
  report: ThresholdReportPayload | null;
  report_computed_at: string | null;
  status: 'ok' | 'error' | null;
  error: string | null;
  computed_at: string | null;
  window_days: number | null;
  target_per_day: number | null;
  duration_s: number | null;
  running: boolean;
}
