"""Prometheus metrics shared across services."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Events consumed / produced, labeled by service + topic + event type.
# The exposed sample names are constants because a second copy of these strings
# is what broke the Command Center graph: the health collector scraped
# `events_consumed_total` and always read 0.
EVENTS_CONSUMED_METRIC = "cmi_events_consumed_total"
EVENTS_PRODUCED_METRIC = "cmi_events_produced_total"

EVENTS_CONSUMED = Counter(
    EVENTS_CONSUMED_METRIC,
    "Number of events consumed",
    ["service", "topic", "event_type"],
)
EVENTS_PRODUCED = Counter(
    EVENTS_PRODUCED_METRIC,
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
#: A reading a collector set out to obtain and could not. Distinct from
#: UPSTREAM_REQUESTS{status="error"}, which counts *calls*: a lookup can fail
#: after a perfectly successful HTTP request — DefiLlama's unlock parser raises
#: on a schedule it cannot size, and that is recorded as `ok` by the request
#: counter because the request was fine.
#:
#: This matters more here than in most pipelines. The scoring model excludes an
#: absent axis rather than penalising it, so a reading we failed to take pushes
#: scores *up* while leaving no trace in any error metric. Without this counter
#: the only signal is a WARNING line, which sits below most alert thresholds.
UNMEASURED = Counter(
    "cmi_unmeasured_total",
    "Readings a collector could not obtain, by field and cause",
    ["service", "field", "reason"],
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

#: Ticks de tache periodique, par issue. Le nom est une constante parce qu'une
#: seconde copie de ces chaines est ce qui avait casse le graphe du Command
#: Center: le collecteur scrutait `events_consumed_total` quand le compteur
#: s'appelle `cmi_events_consumed_total`, et servait « 0 » pour « non mesure ».
PERIODIC_TICKS_METRIC = "cmi_periodic_ticks_total"

PERIODIC_TICKS = Counter(
    PERIODIC_TICKS_METRIC,
    "Periodic task ticks by outcome",
    ["service", "task", "status"],
)
