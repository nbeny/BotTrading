"""Prometheus metrics shared across services."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Events consumed / produced, labeled by service + topic + event type.
EVENTS_CONSUMED = Counter(
    "cmi_events_consumed_total",
    "Number of events consumed",
    ["service", "topic", "event_type"],
)
EVENTS_PRODUCED = Counter(
    "cmi_events_produced_total",
    "Number of events produced",
    ["service", "topic", "event_type"],
)
EVENT_PROCESSING_SECONDS = Histogram(
    "cmi_event_processing_seconds",
    "Time spent handling a single event",
    ["service", "event_type"],
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
UPSTREAM_REQUESTS = Counter(
    "cmi_upstream_requests_total",
    "Outbound provider API calls",
    ["service", "provider", "status"],
)
LEXICON_COINS = Gauge(
    "cmi_lexicon_coins",
    "Tokens in the symbol lexicon a collector is currently resolving against",
    ["service"],
)
CONTENT_DROPPED = Counter(
    "cmi_content_dropped_total",
    "Ingested items rejected by the crypto relevance gate",
    ["service", "source", "reason"],
)
INFLIGHT = Gauge(
    "cmi_inflight_tasks",
    "In-flight async tasks",
    ["service"],
)
AI_TOKENS = Counter(
    "cmi_ai_tokens_total",
    "Claude tokens consumed",
    ["service", "model", "direction"],
)
AI_CLI_CALLS = Counter(
    "cmi_ai_cli_calls_total",
    "Claude CLI subprocess invocations by outcome",
    ["service", "model", "outcome"],
)
AI_MODEL_TIER_MISMATCH = Counter(
    "cmi_ai_model_tier_mismatch_total",
    "CLI served a different model family than requested",
    ["service", "requested_tier", "actual_tier"],
)
