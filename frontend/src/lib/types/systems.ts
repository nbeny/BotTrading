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
  throughput_per_min: number;
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
  throughput_per_min: number;
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
