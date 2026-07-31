/**
 * Observability models for the "Systèmes" page — a live map of every
 * microservice, Kafka topic, collector source, AI worker and infra dependency
 * behind the CMI platform. In live mode these would be fed by each service's
 * `/health` + `/metrics` endpoints (aggregated); for now the mock BFF supplies
 * plausible, coherent snapshots.
 */

export type ServiceHealth = 'healthy' | 'degraded' | 'down' | 'idle';

/** Logical lanes of the pipeline, left → right. */
export type ServiceGroup = 'edge' | 'collect' | 'analyze' | 'decide' | 'execute' | 'infra';

export interface ServiceNode {
  id: string;
  name: string;
  group: ServiceGroup;
  role: string;
  status: ServiceHealth;
  version: string;
  replicas: number;
  uptime_pct: number;
  latency_ms: number;
  cpu_pct: number;
  mem_mb: number;
  /** null until two /metrics scrapes exist — never conflate "not measured" with 0. */
  throughput_per_min: number | null;
  kafka_in: string[];
  kafka_out: string[];
  host: string | null;
  /** last few requests/s samples for the sparkline */
  spark: number[];
}

export interface KafkaTopic {
  name: string;
  partitions: number;
  msg_per_min: number;
  lag: number;
  consumers: number;
  retention_h: number;
  bytes_per_min: number;
  orphaned: boolean;
}

export interface CollectorSource {
  platform: string;
  category: 'social' | 'news' | 'market';
  status: ServiceHealth;
  poll_interval_s: number;
  items_last_hour: number;
  rate_limit_pct: number;
  key_gated: boolean;
  enabled: boolean;
  last_item_ago_s: number;
}

export interface AiWorker {
  name: string;
  model: string;
  tier: 'triage' | 'senior';
  status: ServiceHealth;
  requests_last_hour: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd_today: number;
  avg_latency_ms: number;
  escalation_rate: number;
  queue_depth: number;
}

export interface InfraResource {
  id: string;
  name: string;
  kind: 'database' | 'cache' | 'broker' | 'proxy';
  status: ServiceHealth;
  metrics: { label: string; value: string; pct?: number }[];
}

/** Nodes of the horizontal flow diagram. */
export interface PipelineStage {
  id: string;
  label: string;
  sublabel: string;
  status: ServiceHealth;
  /** null until two /metrics scrapes exist — never conflate "not measured" with 0. */
  throughput_per_min: number | null;
  /** Items that crossed this stage over the selected window. */
  volume: number | null;
  /** Rejected here, or still pending (sentiment backlog, Sonnet budget). */
  dropped: number | null;
  /** Survival from the previous stage; null where the two count different units. */
  conversion_pct: number | null;
  last_at: string | null;
  last_summary: string | null;
}

export type SystemsWindow = '1h' | '24h' | '7d';

export interface StageItem {
  at: string | null;
  symbol: string | null;
  summary: string;
  detail: Record<string, string | number | boolean | null>;
  /** When set, the row opens the existing DecisionTraceDrawer. */
  correlation_id: string | null;
}

export interface StageDetail {
  id: string;
  label: string;
  window: string;
  volume: number | null;
  dropped: number | null;
  breakdown: { key: string; count: number }[];
  items: StageItem[];
  updated_at: string;
}

export interface SystemsSummary {
  services_total: number;
  services_healthy: number;
  services_degraded: number;
  services_down: number;
  events_per_min: number;
  kafka_lag_total: number;
  ai_cost_today_usd: number;
  global_uptime_pct: number;
  data_points_today: number;
  updated_at: string;
}

export interface SystemsSnapshot {
  summary: SystemsSummary;
  services: ServiceNode[];
  pipeline: PipelineStage[];
  kafka: KafkaTopic[];
  collectors: CollectorSource[];
  workers: AiWorker[];
  infra: InfraResource[];
  pipeline_window: string;
  pipeline_stale: boolean;
}

export const HEALTH_LABEL: Record<ServiceHealth, string> = {
  healthy: 'Opérationnel',
  degraded: 'Dégradé',
  down: 'Hors ligne',
  idle: 'Inactif',
};

export const GROUP_LABEL: Record<ServiceGroup, string> = {
  edge: 'Passerelle',
  collect: 'Collecte',
  analyze: 'Analyse IA',
  decide: 'Décision',
  execute: 'Exécution',
  infra: 'Infrastructure',
};

/**
 * Pipeline funnel — where signals die between raw analyses and executed
 * trades. Fed by `GET /systems/funnel`; this is the diagnostic the whole
 * "phase 1" effort exists to surface (see FunnelPanel).
 */
export interface FunnelStage {
  stage: string;
  count: number;
  conversion_pct: number;
}

export interface FunnelBlockReason {
  stage: string;
  reason: string;
  count: number;
}

export interface FunnelStats {
  window: string;
  stages: FunnelStage[];
  score_histogram: { bucket: number; count: number }[];
  factors_presence: Record<string, number>;
  top_block_reasons: FunnelBlockReason[];
  /** True when reasons beyond the top ones were dropped from the list. */
  block_reasons_truncated: boolean;
  updated_at: string;
}
