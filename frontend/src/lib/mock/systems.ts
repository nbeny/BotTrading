/**
 * Mock generator for the "Systèmes" observability page. Produces a coherent,
 * lightly-jittered snapshot of the whole CMI runtime (every service described
 * in CLAUDE.md), so the page feels live under polling. Values are plausible,
 * not random noise — a degraded service stays degraded, throughputs track the
 * pipeline shape, and orphaned Kafka topics are flagged as such.
 */
import { rand, round } from './universe';
import type {
  AiWorker,
  CollectorSource,
  InfraResource,
  KafkaTopic,
  PipelineStage,
  ServiceHealth,
  ServiceNode,
  SystemsSnapshot,
  SystemsSummary,
} from '@/lib/types/systems';

const jitter = (base: number, pct = 0.12) => round(base * (1 + rand(-pct, pct)), 1);
const spark = (base: number) => Array.from({ length: 12 }, () => round(base * rand(0.6, 1.4), 1));

// ── Services (nodes of the map + inputs to the flow) ──────────────────────────
function services(): ServiceNode[] {
  const defs: Omit<ServiceNode, 'spark' | 'latency_ms' | 'cpu_pct' | 'throughput_per_min'>[] = [
    {
      id: 'traefik', name: 'Traefik', group: 'edge', role: 'Reverse proxy / TLS',
      status: 'healthy', version: 'v3.1', replicas: 1, uptime_pct: 99.98,
      mem_mb: 92, kafka_in: [], kafka_out: [], host: 'edge.cmi',
    },
    {
      id: 'coingecko', name: 'collector-coingecko', group: 'collect', role: 'Prix & volumes CEX',
      status: 'healthy', version: '1.4.2', replicas: 1, uptime_pct: 99.7,
      mem_mb: 148, kafka_in: [], kafka_out: ['price.events', 'volume.events'], host: 'collect-01',
    },
    {
      id: 'dexscreener', name: 'collector-dexscreener', group: 'collect', role: 'Liquidité DEX on-chain',
      status: 'healthy', version: '1.2.0', replicas: 1, uptime_pct: 99.4,
      mem_mb: 132, kafka_in: [], kafka_out: ['dex.events'], host: 'collect-01',
    },
    {
      id: 'collector-social', name: 'collector-social', group: 'collect', role: 'Bluesky · Reddit · Mastodon · 4chan · Farcaster · YouTube · Lens',
      status: 'degraded', version: '2.1.0', replicas: 1, uptime_pct: 98.1,
      mem_mb: 312, kafka_in: [], kafka_out: [], host: 'collect-02',
    },
    {
      id: 'collector-news', name: 'collector-news', group: 'collect', role: 'CryptoCompare · RSS · GDELT · NewsData',
      status: 'healthy', version: '2.0.3', replicas: 1, uptime_pct: 99.5,
      mem_mb: 208, kafka_in: [], kafka_out: [], host: 'collect-02',
    },
    {
      id: 'sentiment', name: 'sentiment-service', group: 'analyze', role: 'Scoring L1 (CryptoBERT/FinBERT/RoBERTa)',
      status: 'healthy', version: '3.2.1', replicas: 2, uptime_pct: 99.6,
      mem_mb: 1240, kafka_in: [], kafka_out: ['sentiment.events'], host: 'gpu-01',
    },
    {
      id: 'ai-haiku', name: 'ai-worker-haiku', group: 'analyze', role: 'Triage — Claude Haiku',
      status: 'healthy', version: '4.0.0', replicas: 3, uptime_pct: 99.9,
      mem_mb: 196, kafka_in: ['price.events', 'volume.events', 'dex.events', 'sentiment.events'], kafka_out: ['analysis.events'], host: 'ai-01',
    },
    {
      id: 'ai-sonnet', name: 'ai-worker-sonnet', group: 'analyze', role: 'Analyste senior — Claude Sonnet',
      status: 'healthy', version: '4.0.0', replicas: 2, uptime_pct: 99.8,
      mem_mb: 224, kafka_in: ['analysis.events'], kafka_out: ['decision.events'], host: 'ai-01',
    },
    {
      id: 'decision', name: 'decision-engine', group: 'decide', role: 'Fusion signaux → intention',
      status: 'healthy', version: '2.4.0', replicas: 2, uptime_pct: 99.7,
      mem_mb: 176, kafka_in: ['decision.events'], kafka_out: ['risk.pending.events'], host: 'core-01',
    },
    {
      id: 'risk', name: 'risk-engine', group: 'decide', role: 'Garde-fous & validation',
      status: 'healthy', version: '2.4.0', replicas: 2, uptime_pct: 99.9,
      mem_mb: 168, kafka_in: ['risk.pending.events'], kafka_out: ['risk.approved.events'], host: 'core-01',
    },
    {
      id: 'trading-engine', name: 'trading-engine', group: 'execute', role: 'Exécution — Kraken Futures',
      status: 'healthy', version: '1.8.0', replicas: 2, uptime_pct: 99.95,
      mem_mb: 204, kafka_in: ['risk.approved.events', 'control.commands'], kafka_out: ['execution.events'], host: 'core-02',
    },
    {
      id: 'control-api', name: 'control-api', group: 'execute', role: 'Plan de contrôle (JWT · commandes)',
      status: 'healthy', version: '1.6.0', replicas: 2, uptime_pct: 99.9,
      mem_mb: 156, kafka_in: [], kafka_out: ['control.commands'], host: 'api-01',
    },
    {
      id: 'api-gateway', name: 'api-gateway', group: 'edge', role: 'REST lecture seule (Kafka→Postgres)',
      status: 'healthy', version: '1.6.0', replicas: 2, uptime_pct: 99.9,
      mem_mb: 172, kafka_in: ['analysis.events', 'decision.events', 'risk.approved.events', 'execution.events'], kafka_out: [], host: 'api-01',
    },
    {
      id: 'websocket-gateway', name: 'websocket-gateway', group: 'edge', role: 'Diffusion temps réel (WS)',
      status: 'healthy', version: '1.3.0', replicas: 2, uptime_pct: 99.8,
      mem_mb: 188, kafka_in: ['10 topics'], kafka_out: [], host: 'api-01',
    },
  ];

  const load: Record<string, number> = {
    coingecko: 240, dexscreener: 180, 'collector-social': 92, 'collector-news': 64,
    sentiment: 320, 'ai-haiku': 410, 'ai-sonnet': 88, decision: 120, risk: 96,
    'trading-engine': 14, 'control-api': 6, 'api-gateway': 520, 'websocket-gateway': 640,
    traefik: 1200,
  };

  return defs.map((d) => {
    const base = load[d.id] ?? 40;
    const degraded = d.status === 'degraded';
    return {
      ...d,
      latency_ms: round(jitter(degraded ? rand(280, 520) : rand(18, 140)), 0),
      cpu_pct: round(jitter(degraded ? rand(60, 88) : rand(6, 48)), 0),
      throughput_per_min: round(jitter(base), 0),
      spark: spark(base),
    };
  });
}

// ── Kafka topics ──────────────────────────────────────────────────────────────
function kafka(): KafkaTopic[] {
  const defs: [string, number, number, number, number, boolean][] = [
    // name, partitions, msg/min base, consumers, retention_h, orphaned
    ['price.events', 6, 240, 3, 24, false],
    ['volume.events', 6, 240, 3, 24, false],
    ['dex.events', 4, 180, 3, 24, false],
    ['sentiment.events', 4, 96, 2, 48, false],
    ['analysis.events', 6, 410, 2, 48, false],
    ['decision.events', 4, 88, 2, 72, false],
    ['risk.pending.events', 3, 62, 2, 72, false],
    ['risk.approved.events', 3, 18, 1, 168, false],
    ['execution.events', 3, 14, 3, 168, false],
    ['control.commands', 2, 6, 4, 168, false],
    ['social.events', 3, 0, 0, 24, true],
    ['news.events', 3, 0, 0, 24, true],
  ];
  return defs.map(([name, partitions, base, consumers, retention_h, orphaned]) => {
    const msg = orphaned ? 0 : round(jitter(base), 0);
    return {
      name,
      partitions,
      msg_per_min: msg,
      lag: orphaned ? 0 : Math.max(0, Math.round(rand(0, base > 200 ? 140 : 24))),
      consumers,
      retention_h,
      bytes_per_min: round(msg * rand(180, 640), 0),
      orphaned,
    };
  });
}

// ── Collector sources ─────────────────────────────────────────────────────────
function collectors(): CollectorSource[] {
  const defs: [string, CollectorSource['category'], boolean, boolean, ServiceHealth][] = [
    // platform, category, key_gated, enabled, status
    ['Bluesky', 'social', false, true, 'healthy'],
    ['Reddit', 'social', false, true, 'healthy'],
    ['Mastodon', 'social', false, true, 'healthy'],
    ['4chan /biz/', 'social', false, true, 'degraded'],
    ['Farcaster (Neynar)', 'social', true, true, 'healthy'],
    ['YouTube', 'social', true, true, 'healthy'],
    ['Lens', 'social', false, true, 'healthy'],
    ['CryptoCompare', 'news', false, true, 'healthy'],
    ['RSS', 'news', false, true, 'healthy'],
    ['GDELT', 'news', false, true, 'healthy'],
    ['NewsData', 'news', true, true, 'healthy'],
    ['CoinGecko', 'market', false, true, 'healthy'],
    ['DexScreener', 'market', false, true, 'healthy'],
    ['Telegram', 'social', true, false, 'idle'],
    ['StockTwits', 'social', true, false, 'idle'],
    ['Messari', 'news', true, false, 'idle'],
  ];
  return defs.map(([platform, category, key_gated, enabled, status]) => ({
    platform,
    category,
    key_gated,
    enabled,
    status,
    poll_interval_s: enabled ? Math.round(rand(20, 180)) : 0,
    items_last_hour: enabled ? Math.round(jitter(rand(30, 480))) : 0,
    rate_limit_pct: enabled ? Math.round(status === 'degraded' ? rand(78, 96) : rand(8, 62)) : 0,
    last_item_ago_s: enabled ? Math.round(rand(2, status === 'degraded' ? 900 : 120)) : 0,
  }));
}

// ── AI workers ──────────────────────────────────────────────────────────────
function workers(): AiWorker[] {
  const haikuReq = Math.round(jitter(1420));
  const sonnetReq = Math.round(jitter(210));
  return [
    {
      name: 'ai-worker-haiku', model: 'claude-haiku-4-5', tier: 'triage', status: 'healthy',
      requests_last_hour: haikuReq, tokens_in: haikuReq * 820, tokens_out: haikuReq * 190,
      cost_usd_today: round(jitter(6.4), 0.05), avg_latency_ms: Math.round(jitter(640)),
      escalation_rate: round(rand(0.12, 0.22), 2), queue_depth: Math.round(rand(0, 12)),
    },
    {
      name: 'ai-worker-sonnet', model: 'claude-sonnet-4-6', tier: 'senior', status: 'healthy',
      requests_last_hour: sonnetReq, tokens_in: sonnetReq * 2400, tokens_out: sonnetReq * 640,
      cost_usd_today: round(jitter(18.9), 0.05), avg_latency_ms: Math.round(jitter(2100)),
      escalation_rate: round(rand(0.28, 0.44), 2), queue_depth: Math.round(rand(0, 5)),
    },
  ];
}

// ── Infra dependencies ─────────────────────────────────────────────────────────
function infra(): InfraResource[] {
  return [
    {
      id: 'postgres', name: 'PostgreSQL + TimescaleDB', kind: 'database', status: 'healthy',
      metrics: [
        { label: 'Connexions', value: `${Math.round(rand(24, 68))} / 200`, pct: rand(12, 34) },
        { label: 'Taille', value: `${round(rand(38, 44), 1)} GB` },
        { label: 'raw_content', value: `${(rand(2.1, 2.4)).toFixed(2)} M lignes` },
        { label: 'Req/s', value: `${Math.round(rand(180, 420))}` },
      ],
    },
    {
      id: 'redis', name: 'Redis', kind: 'cache', status: 'healthy',
      metrics: [
        { label: 'Mémoire', value: `${Math.round(rand(180, 320))} MB`, pct: rand(18, 42) },
        { label: 'Clés trading:*', value: `${Math.round(rand(40, 120))}` },
        { label: 'Hit rate', value: `${Math.round(rand(94, 99))}%`, pct: rand(94, 99) },
        { label: 'Ops/s', value: `${Math.round(rand(600, 1800))}` },
      ],
    },
    {
      id: 'kafka', name: 'Kafka', kind: 'broker', status: 'healthy',
      metrics: [
        { label: 'Brokers', value: '3 / 3' },
        { label: 'Topics', value: '12' },
        { label: 'Débit', value: `${round(rand(1.8, 3.4), 1)} MB/s` },
        { label: 'Lag total', value: `${Math.round(rand(80, 260))}` },
      ],
    },
    {
      id: 'traefik', name: 'Traefik', kind: 'proxy', status: 'healthy',
      metrics: [
        { label: 'Req/s', value: `${Math.round(rand(20, 60))}` },
        { label: 'Routes', value: '4 hosts' },
        { label: 'TLS', value: 'Let’s Encrypt' },
        { label: 'p99', value: `${Math.round(rand(12, 40))} ms` },
      ],
    },
  ];
}

// ── Pipeline flow stages ────────────────────────────────────────────────────────
function pipeline(svc: ServiceNode[]): PipelineStage[] {
  const tp = (id: string) => svc.find((s) => s.id === id)?.throughput_per_min ?? 0;
  return [
    { id: 'collect', label: 'Collecte', sublabel: 'Marché · Social · News', status: 'degraded', throughput_per_min: tp('coingecko') + tp('collector-social') + tp('collector-news') },
    { id: 'sentiment', label: 'Sentiment', sublabel: 'Scoring L1', status: 'healthy', throughput_per_min: tp('sentiment') },
    { id: 'triage', label: 'Triage', sublabel: 'Haiku', status: 'healthy', throughput_per_min: tp('ai-haiku') },
    { id: 'senior', label: 'Analyse', sublabel: 'Sonnet', status: 'healthy', throughput_per_min: tp('ai-sonnet') },
    { id: 'decision', label: 'Décision', sublabel: 'Fusion signaux', status: 'healthy', throughput_per_min: tp('decision') },
    { id: 'risk', label: 'Risque', sublabel: 'Garde-fous', status: 'healthy', throughput_per_min: tp('risk') },
    { id: 'execute', label: 'Exécution', sublabel: 'Kraken Futures', status: 'healthy', throughput_per_min: tp('trading-engine') },
  ];
}

function summary(svc: ServiceNode[], kf: KafkaTopic[], wk: AiWorker[]): SystemsSummary {
  const healthy = svc.filter((s) => s.status === 'healthy').length;
  const degraded = svc.filter((s) => s.status === 'degraded').length;
  const down = svc.filter((s) => s.status === 'down').length;
  return {
    services_total: svc.length,
    services_healthy: healthy,
    services_degraded: degraded,
    services_down: down,
    events_per_min: Math.round(kf.reduce((s, t) => s + t.msg_per_min, 0)),
    kafka_lag_total: kf.reduce((s, t) => s + t.lag, 0),
    ai_cost_today_usd: round(wk.reduce((s, w) => s + w.cost_usd_today, 0), 2),
    global_uptime_pct: round(svc.reduce((s, x) => s + x.uptime_pct, 0) / svc.length, 2),
    data_points_today: Math.round(rand(1.8, 2.2) * 1_000_000),
    updated_at: new Date().toISOString(),
  };
}

export function getSystemsSnapshot(): SystemsSnapshot {
  const svc = services();
  const kf = kafka();
  const wk = workers();
  return {
    summary: summary(svc, kf, wk),
    services: svc,
    pipeline: pipeline(svc),
    kafka: kf,
    collectors: collectors(),
    workers: wk,
    infra: infra(),
  };
}
