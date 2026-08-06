import type { DecisionTrace } from './content';
import type { PipelineVerdict, TokenScore } from './dossier';

/** Facteurs de triage Haiku — namespace DISJOINT des 8 axes de scoring. */
export interface ExplainTriage {
  score: number | null;
  confidence: number | null;
  factors: Record<string, number>;
  dominant_factor: string | null;
  escalated: boolean;
  sonnet_called: boolean;
  sonnet_validated: boolean | null;
  sonnet_score: number | null;
  sonnet_direction: string | null;
  skip_reason: string | null;
}

export interface ExplainCounterfactual {
  horizon: string;
  pnl_pct: number | null;
  outcome: string | null;
}

export interface DecisionExplain {
  id: string;
  symbol: string | null;
  direction: string | null;
  /** Échelle brute 0–100 — l'inspecteur n'affiche jamais la 0–1. */
  score: TokenScore;
  triage: ExplainTriage | null;
  risk: { verdict: string | null; reason: string | null } | null;
  pipeline: PipelineVerdict;
  counterfactual: ExplainCounterfactual | null;
  trace: DecisionTrace | null;
  correlation_id: string | null;
}
