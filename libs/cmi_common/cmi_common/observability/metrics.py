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
