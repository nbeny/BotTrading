import { pick, rand, round, uid, UNIVERSE } from './universe';
import type { ContentPage, ContentQuery, DataStats, RawContentItem } from '@/lib/types/content';

const SOCIAL = ['Reddit', 'Bluesky', 'Mastodon', '4chan /biz/', 'Farcaster', 'YouTube', 'Lens'];
const NEWS = ['CoinDesk', 'CoinTelegraph', 'The Block', 'Decrypt', 'GDELT', 'RSS', 'NewsData'];
const MARKET = ['CoinGecko', 'DexScreener'];
const SNIPPETS = [
  'ETF inflows accelerating, whales accumulating on-chain',
  'liquidations en cascade si le support cède',
  'momentum fort, volume 2× la moyenne 30j',
  'partenariat institutionnel annoncé ce matin',
  'MiCA entre en vigueur en Europe',
  'breakout de résistance majeure confirmé',
  'sentiment social en forte hausse sur 24h',
  'profondeur de carnet faible, prudence',
];

function iso(offsetMs: number) { return new Date(Date.now() + offsetMs).toISOString(); }
function catOf(i: number): RawContentItem['source_category'] { return i % 5 < 3 ? 'social' : i % 5 === 3 ? 'news' : 'market'; }
function platformFor(cat: RawContentItem['source_category']) {
  return cat === 'social' ? pick(SOCIAL) : cat === 'news' ? pick(NEWS) : pick(MARKET);
}

const _pool: RawContentItem[] = Array.from({ length: 400 }, (_, i) => {
  const cat = catOf(i);
  const tok = pick(UNIVERSE);
  const s = round(rand(-0.8, 0.9), 2);
  const hasDecision = s > 0.55 || s < -0.45;
  return {
    id: uid('rc'),
    platform: platformFor(cat),
    source_category: cat,
    symbols: [tok.symbol, ...(Math.random() > 0.7 ? [pick(UNIVERSE).symbol] : [])],
    title: `${tok.symbol} — ${pick(SNIPPETS)}`,
    snippet: pick(SNIPPETS),
    url: `https://example.com/${uid('u')}`,
    published_at: iso(-rand(0, 24 * 3600_000)),
    collected_at: iso(-rand(0, 3600_000)),
    sentiment_score: s,
    sentiment_confidence: round(rand(0.55, 0.98), 2),
    model_name: cat === 'news' ? 'ProsusAI/finbert' : 'ElKulako/cryptobert',
    sample_size: Math.floor(rand(10, 300)),
    derived_decision: hasDecision
      ? { direction: (s > 0 ? 'long' : 'short') as 'long' | 'short', opportunity_score: Math.floor(rand(60, 92)), correlation_id: uid('corr') }
      : null,
  };
}).sort((a, b) => +new Date(b.collected_at) - +new Date(a.collected_at));

export function queryContent(q: ContentQuery): ContentPage {
  const limit = q.limit ?? 50;
  const offset = q.offset ?? 0;
  let items = _pool;
  if (q.category && q.category !== 'all') items = items.filter((x) => x.source_category === q.category);
  if (q.symbol) items = items.filter((x) => x.symbols.includes(q.symbol!.toUpperCase()));
  if (q.sentiment && q.sentiment !== 'all') {
    items = items.filter((x) =>
      q.sentiment === 'pos' ? x.sentiment_score > 0.15 : q.sentiment === 'neg' ? x.sentiment_score < -0.15 : Math.abs(x.sentiment_score) <= 0.15);
  }
  if (q.q) { const t = q.q.toLowerCase(); items = items.filter((x) => (x.title + x.snippet).toLowerCase().includes(t)); }
  return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
}

export function contentStats(): DataStats {
  const social = _pool.filter((x) => x.source_category === 'social').length;
  const news = _pool.filter((x) => x.source_category === 'news').length;
  const market = _pool.filter((x) => x.source_category === 'market').length;
  const avg = round(_pool.reduce((s, x) => s + x.sentiment_score, 0) / _pool.length, 2);
  const hours = Array.from({ length: 12 }, (_, h) => 11 - h);
  const volume_series = hours.map((h) => ({
    hour: `${String((new Date().getHours() - h + 24) % 24).padStart(2, '0')}h`,
    social: Math.round(rand(40, 160)), news: Math.round(rand(10, 60)), market: Math.round(rand(20, 80)),
  }));
  const sentiment_series = hours.map((h) => ({
    hour: `${String((new Date().getHours() - h + 24) % 24).padStart(2, '0')}h`, sentiment: round(rand(-0.3, 0.6), 2),
  }));
  const srcCount: Record<string, number> = {};
  _pool.forEach((x) => { srcCount[x.platform] = (srcCount[x.platform] ?? 0) + 1; });
  const top_sources = Object.entries(srcCount).map(([source, count]) => ({ source, count })).sort((a, b) => b.count - a.count).slice(0, 6);
  const menCount: Record<string, number> = {};
  _pool.forEach((x) => x.symbols.forEach((s) => { menCount[s] = (menCount[s] ?? 0) + 1; }));
  const mentions = Object.entries(menCount).map(([symbol, count]) => ({ symbol, count })).sort((a, b) => b.count - a.count).slice(0, 8);
  return { total_24h: _pool.length, social_24h: social, news_24h: news, market_24h: market,
    avg_sentiment: avg, volume_series, sentiment_series, top_sources, mentions, updated_at: new Date().toISOString() };
}
