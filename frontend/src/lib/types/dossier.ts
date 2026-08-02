import type { NewsItem, Position, Trade, WorkerDecision } from './domain';

/**
 * Les sept axes de decision-engine/app/scoring.py::WEIGHTS, dans l'ordre
 * d'affichage du drawer.
 *
 * Troisième copie indépendante de cette liste, avec
 * `decision-engine/app/scoring.py::WEIGHTS` et
 * `services/api-gateway/app/dossier.py::AXIS_KEYS`. Rien ne vérifie qu'elles
 * restent alignées : un axe ajouté au scoring sans être ajouté ici
 * n'apparaîtrait jamais à l'écran, sans erreur ni test rouge.
 */
export const SCORE_AXES = [
  'volume_growth',
  'social_score',
  'news_score',
  'market_trend',
  'liquidity_score',
  'positioning',
  'fundamentals',
  'developer_activity',
] as const;

export type ScoreAxis = (typeof SCORE_AXES)[number];

export const AXIS_LABELS: Record<ScoreAxis, string> = {
  volume_growth: 'Volume',
  social_score: 'Social',
  news_score: 'News',
  market_trend: 'Tendance',
  liquidity_score: 'Liquidité',
  positioning: 'Positionnement',
  fundamentals: 'Fondamentaux',
  developer_activity: 'Développement',
};

export interface TokenScore {
  /**
   * Sur **0–100** — le `Decision.opportunity_score` brut, rendu tel quel à côté
   * d'un « / 100 ». Attention au voisin : `TokenDossier.decisions[].opportunity_score`
   * est sur 0–1 dans la même réponse (`map_decision` divise), et `ScoreChip`
   * remultiplie. Deux échelles pour « score d'opportunité » dans un même payload.
   */
  value: number | null;
  confidence: number | null;
  /**
   * Seuls les axes **mesurés** sont présents. Une clé absente signifie « non
   * mesuré » : le scoring renormalise sur le poids présent, donc l'axe est
   * exclu du calcul, pas noté zéro. Le type est `Partial` exprès — il force
   * l'appelant à traiter `undefined`, ce qu'un `Record` complet masquerait.
   */
  axes: Partial<Record<ScoreAxis, number>>;
  axes_total: number;
  /**
   * `true` : une décision existe, mais le poids des axes présents était sous le
   * seuil de renormalisation — le back renvoie alors `value: null` plutôt que le
   * `0` que le moteur de scoring produit dans ce cas. `false` quand aucune
   * décision n'existe : rien n'a été tenté, ce n'est pas la même chose.
   */
  insufficient_evidence: boolean;
  computed_at: string | null;
}

export interface PipelineVerdict {
  /** Vocabulaire de systems_pipeline.py::STAGE_SPECS. */
  reached_stage: string | null;
  /** `null` = aucun blocage observé. Ce n'est pas « passé » : ce peut être un
   *  signal encore en vol. L'UI doit distinguer les deux. */
  blocked_at: string | null;
  block_reason: string | null;
  /** `null` quand la seule trace est un rejet sans ligne de journal : on ignore
   *  alors si Haiku avait escaladé. Ne pas rendre `null` comme « non ». */
  escalated: boolean | null;
  sonnet_called: boolean | null;
  sonnet_validated: boolean | null;
  last_event_at: string | null;
}

export interface TokenExposure {
  open_positions: Position[];
  recent_trades: Trade[];
}

export interface TokenDossier {
  symbol: string;
  score: TokenScore;
  pipeline: PipelineVerdict;
  decisions: WorkerDecision[];
  /** News **et** social mentionnant le symbole. */
  content: NewsItem[];
  exposure: TokenExposure;
}

/**
 * Valeur d'un axe, `undefined` normalisé en `null`.
 *
 * Exister sous cette forme est le point : `axes[axis] ?? 0` est l'erreur que ce
 * projet a déjà commise sous d'autres formes, et elle ne lève rien. Passer par
 * ce helper rend l'absence explicite au site d'appel.
 */
export function axisValue(
  axes: TokenScore['axes'],
  axis: ScoreAxis,
): number | null {
  const v = axes[axis];
  return v === undefined ? null : v;
}
