"""Guard against metric-name drift between producer and consumer.

The Command Center graph reported `0/m` on every connector for one reason: the
health collector summed `events_consumed_total` while the counters are named
`cmi_events_consumed_total`. A test asserting hard-coded strings would have
passed against that broken code. This one renders the *real* Prometheus registry
and requires a non-zero rate to come out the other end.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, generate_latest
from service_modules import load_service_module

from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

health_collector = load_service_module("api-gateway", "health_collector")


def _scrape() -> dict:
    return health_collector.parse_prometheus(generate_latest(REGISTRY).decode())


def test_throughput_is_derived_from_the_real_counter_names() -> None:
    EVENTS_CONSUMED.labels("probe-svc", "topic", "type").inc(10)
    EVENTS_PRODUCED.labels("probe-svc", "topic", "type").inc(5)
    first, sample = health_collector.compute_detail(_scrape(), None, 0.0)

    # One sample is not a rate: reporting anything here would be an invention.
    assert "throughput_per_min" not in first

    EVENTS_CONSUMED.labels("probe-svc", "topic", "type").inc(40)
    EVENTS_PRODUCED.labels("probe-svc", "topic", "type").inc(20)
    second, _ = health_collector.compute_detail(_scrape(), sample, 60.0)

    # 60 new events over 60s == 60/min.
    assert second["throughput_per_min"] == 60


def test_counter_name_constants_match_the_exposed_samples() -> None:
    from cmi_common.observability import (
        EVENTS_CONSUMED_METRIC,
        EVENTS_PRODUCED_METRIC,
    )

    scraped = _scrape()
    assert EVENTS_CONSUMED_METRIC in scraped
    assert EVENTS_PRODUCED_METRIC in scraped
