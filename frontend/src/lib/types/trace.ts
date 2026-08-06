/**
 * Étiquettes des `stage.kind` d'une `DecisionTrace`, partagées entre
 * `DecisionTraceDrawer` (trace par correlation id) et `DecisionInspector`
 * (trace globale par ?decision=). Auparavant dupliquées à l'identique dans
 * les deux composants — une seule copie évite qu'elles divergent en silence.
 */
export const KIND_LABEL: Record<string, string> = {
  price: 'Prix',
  sentiment: 'Sentiment',
  analysis: 'Haiku',
  decision: 'Sonnet',
  risk: 'Risque',
  order: 'Ordre',
};
