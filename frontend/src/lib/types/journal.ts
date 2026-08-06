export type JournalWindow = '7d' | '30d' | '90d';

export interface JournalRow {
  time: string | null;
  event_id: string;
  symbol: string;
  score: number | null;
  confidence: number | null;
  escalated: boolean;
  sonnet_called: boolean;
  sonnet_validated: boolean | null;
  direction: string | null;
  passed: boolean;
  risk_verdict: string | null;
  pnl_pct: number | null;
  outcome: string | null;
  correlation_id: string | null;
}

export interface JournalDecisionsPage {
  window: JournalWindow;
  horizon: string;
  current_threshold: number | null;
  total: number;
  rows: JournalRow[];
}

export interface CalibrationBucket {
  threshold: number;
  selected: number;
  judged: number;
  sufficient: boolean;
  win_rate: number | null;
  avg_pnl_pct: number | null;
  total_pnl_pct: number | null;
}

export interface JournalCalibration {
  window: JournalWindow;
  horizon: string;
  min_n: number;
  requested: CalibrationBucket;
  current: CalibrationBucket | null;
}

export interface AttributionFactor {
  key: string;
  n: number;
  correlation: number | null;
}

export interface JournalAttribution {
  window: JournalWindow;
  horizon: string;
  n: number;
  min_n: number;
  sufficient: boolean;
  factors: AttributionFactor[];
}
