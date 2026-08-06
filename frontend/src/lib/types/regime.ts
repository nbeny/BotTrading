/** Contrat GET /market/regime — miroir de api-gateway app/regime.py.
 *  Règle du projet : null = non mesuré = rendu « — », jamais 0. */
export type RegimeLabel = 'RISK_ON' | 'ACCUMULATION' | 'NEUTRAL' | 'DISTRIBUTION' | 'RISK_OFF';
export type DriverState = 'bullish' | 'bearish' | 'neutral';
export type DriverKey = 'funding' | 'oi_delta' | 'market_sentiment' | 'btc_dominance' | 'breadth';

export interface RegimeDriver {
  key: DriverKey;
  value: number | null;
  state: DriverState | null;
  detail: string;
  as_of: string | null;
}

export interface MarketRegime {
  regime: RegimeLabel | null;
  confidence: number | null;
  drivers: RegimeDriver[];
  computed_at: string;
}

export const DRIVER_LABELS: Record<DriverKey, string> = {
  funding: 'Funding',
  oi_delta: 'ΔOI 24h',
  market_sentiment: 'Sent. marché',
  btc_dominance: 'BTC.D Δ7j',
  breadth: 'Breadth',
};

export const REGIME_LABELS: Record<RegimeLabel, string> = {
  RISK_ON: 'RISK-ON',
  ACCUMULATION: 'ACCUMULATION',
  NEUTRAL: 'NEUTRE',
  DISTRIBUTION: 'DISTRIBUTION',
  RISK_OFF: 'RISK-OFF',
};
