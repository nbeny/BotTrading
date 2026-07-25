/** A single collected item (mirrors backend raw_content; sentiment aggregates now
 *  live in the additive hourly-bucket content_sentiment_agg, exposed via
 *  /api/v1/sentiment/{windows,series,authors} and overlaid onto DataStats). */
export interface RawContentItem {
  id: string;
  platform: string;
  source_category: 'social' | 'news' | 'market';
  symbols: string[];
  title: string;
  snippet: string;
  url: string | null;
  published_at: string;
  collected_at: string;
  sentiment_score: number;
  sentiment_confidence: number;
  model_name: string;
  sample_size: number;
  derived_decision: {
    direction: 'long' | 'short' | null;
    opportunity_score: number | null;
    correlation_id: string | null;
  } | null;
}

export interface DataStats {
  total_24h: number;
  social_24h: number;
  news_24h: number;
  market_24h: number;
  avg_sentiment: number;
  volume_series: { hour: string; social: number; news: number; market: number }[];
  // Live `/data/stats` sources this from content_sentiment_agg buckets, so `hour`
  // is an ISO timestamp and each point carries `mentions`. The mock omits mentions.
  sentiment_series: { hour: string; sentiment: number; mentions?: number }[];
  top_sources: { source: string; count: number }[];
  mentions: { symbol: string; count: number }[];
  updated_at: string;
}

export type ContentCategory = 'all' | 'social' | 'news' | 'market';

export interface ContentQuery {
  category?: ContentCategory;
  symbol?: string;
  q?: string;
  sentiment?: 'all' | 'pos' | 'neg' | 'neu';
  limit?: number;
  offset?: number;
}

export interface ContentPage {
  items: RawContentItem[];
  total: number;
  offset: number;
  limit: number;
}

/** One stage of an end-to-end decision lineage, keyed by correlation_id. */
export interface DecisionTrace {
  correlation_id: string;
  symbol: string;
  stages: {
    kind: 'price' | 'sentiment' | 'analysis' | 'decision' | 'risk' | 'order';
    at: string | null;
    reached: boolean;
    summary: string;
    detail: Record<string, string | number | boolean | null>;
  }[];
}
