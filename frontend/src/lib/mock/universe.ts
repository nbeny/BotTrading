/** Shared token universe + helpers for the standalone demo (mock) mode. */

export interface UniToken {
  symbol: string;
  coin_id: string;
  name: string;
  price: number;
  chain: string;
}

export const UNIVERSE: UniToken[] = [
  { symbol: 'BTC', coin_id: 'bitcoin', name: 'Bitcoin', price: 68420, chain: 'bitcoin' },
  { symbol: 'ETH', coin_id: 'ethereum', name: 'Ethereum', price: 3580, chain: 'ethereum' },
  { symbol: 'SOL', coin_id: 'solana', name: 'Solana', price: 150.2, chain: 'solana' },
  { symbol: 'AVAX', coin_id: 'avalanche-2', name: 'Avalanche', price: 38.4, chain: 'avalanche' },
  { symbol: 'LINK', coin_id: 'chainlink', name: 'Chainlink', price: 17.9, chain: 'ethereum' },
  { symbol: 'ARB', coin_id: 'arbitrum', name: 'Arbitrum', price: 1.12, chain: 'arbitrum' },
  { symbol: 'DOGE', coin_id: 'dogecoin', name: 'Dogecoin', price: 0.162, chain: 'dogecoin' },
  { symbol: 'RNDR', coin_id: 'render-token', name: 'Render', price: 9.8, chain: 'solana' },
  { symbol: 'INJ', coin_id: 'injective-protocol', name: 'Injective', price: 27.3, chain: 'injective' },
  { symbol: 'WIF', coin_id: 'dogwifcoin', name: 'dogwifhat', price: 2.35, chain: 'solana' },
  { symbol: 'BNB', coin_id: 'binancecoin', name: 'BNB', price: 592.1, chain: 'bsc' },
  { symbol: 'XRP', coin_id: 'ripple', name: 'XRP', price: 0.612, chain: 'xrpl' },
  { symbol: 'ADA', coin_id: 'cardano', name: 'Cardano', price: 0.452, chain: 'cardano' },
  { symbol: 'DOT', coin_id: 'polkadot', name: 'Polkadot', price: 6.84, chain: 'polkadot' },
  { symbol: 'MATIC', coin_id: 'matic-network', name: 'Polygon', price: 0.718, chain: 'polygon' },
  { symbol: 'UNI', coin_id: 'uniswap', name: 'Uniswap', price: 7.92, chain: 'ethereum' },
  { symbol: 'AAVE', coin_id: 'aave', name: 'Aave', price: 94.6, chain: 'ethereum' },
  { symbol: 'OP', coin_id: 'optimism', name: 'Optimism', price: 2.41, chain: 'optimism' },
  { symbol: 'ATOM', coin_id: 'cosmos', name: 'Cosmos', price: 8.15, chain: 'cosmos' },
  { symbol: 'NEAR', coin_id: 'near', name: 'NEAR Protocol', price: 5.63, chain: 'near' },
  {
    symbol: 'ICP',
    coin_id: 'internet-computer',
    name: 'Internet Computer',
    price: 11.2,
    chain: 'internet-computer',
  },
  { symbol: 'LTC', coin_id: 'litecoin', name: 'Litecoin', price: 84.3, chain: 'litecoin' },
  { symbol: 'FIL', coin_id: 'filecoin', name: 'Filecoin', price: 5.28, chain: 'filecoin' },
  { symbol: 'SUI', coin_id: 'sui', name: 'Sui', price: 3.87, chain: 'sui' },
  { symbol: 'APT', coin_id: 'aptos', name: 'Aptos', price: 9.14, chain: 'aptos' },
  { symbol: 'TIA', coin_id: 'celestia', name: 'Celestia', price: 6.02, chain: 'celestia' },
  { symbol: 'PEPE', coin_id: 'pepe', name: 'Pepe', price: 0.0000142, chain: 'ethereum' },
  {
    symbol: 'SHIB',
    coin_id: 'shiba-inu',
    name: 'Shiba Inu',
    price: 0.0000221,
    chain: 'ethereum',
  },
  { symbol: 'LDO', coin_id: 'lido-dao', name: 'Lido DAO', price: 1.86, chain: 'ethereum' },
  { symbol: 'MKR', coin_id: 'maker', name: 'Maker', price: 2410, chain: 'ethereum' },
  {
    symbol: 'CRV',
    coin_id: 'curve-dao-token',
    name: 'Curve DAO Token',
    price: 0.412,
    chain: 'ethereum',
  },
];

export const BY_SYMBOL = new Map(UNIVERSE.map((t) => [t.symbol, t]));

export function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

export function round(n: number, d = 2): number {
  const f = 10 ** d;
  return Math.round(n * f) / f;
}

export function uid(prefix = 'id'): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

const REASONS = [
  'Volume spike x{r} avec sentiment social en hausse',
  'Momentum haussier confirmé, liquidité DEX saine',
  'Mentions Reddit +{p}% sur 4h, breakout technique',
  'News positive corroborée par le flux on-chain',
  'Rotation de capitaux vers le narratif, RR favorable',
  'Nouvelle paire DEX à forte liquidité détectée',
];

export function reason(): string {
  return pick(REASONS)
    .replace('{r}', String(Math.floor(rand(2, 6))))
    .replace('{p}', String(Math.floor(rand(80, 220))));
}

export const RISK_MESSAGES = [
  'Exposition sur {s} proche de la limite par actif',
  'Volatilité élevée détectée sur {s}',
  'Corrélation portefeuille en hausse (concentration)',
  'Perte journalière à 60% de la limite quotidienne',
  'Position {s} non protégée (SL manquant)',
];
