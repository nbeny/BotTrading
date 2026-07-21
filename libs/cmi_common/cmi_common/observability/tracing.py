"""OpenTelemetry tracing + Sentry initialization."""

from __future__ import annotations

import logging

from ..config import ObservabilitySettings

logger = logging.getLogger(__name__)


def setup_tracing(service_name: str, settings: ObservabilitySettings) -> None:
    """Configure OTLP tracing and Sentry. Safe to call once at startup.

    Imports are local so services that don't need tracing don't pay the cost.
    """
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.environment,
                traces_sample_rate=0.1,
            )
            logger.info("Sentry initialized for %s", service_name)
        except ImportError:
            logger.warning("sentry_sdk not installed; skipping Sentry")

    if not settings.tracing_enabled:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.exporter_otlp_endpoint)
            )
        )
        trace.set_tracer_provider(provider)
        logger.info("OpenTelemetry tracing configured for %s", service_name)
    except ImportError:
        logger.warning("opentelemetry not fully installed; skipping tracing")
