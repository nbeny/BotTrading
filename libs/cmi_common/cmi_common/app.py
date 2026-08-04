"""Shared FastAPI application factory.

Every service builds its ASGI app via :func:`create_app`, which wires in the
mandatory ``/health`` and ``/metrics`` endpoints, structured logging, tracing
and a lifespan hook for service-specific startup/shutdown (Kafka, DB, Redis).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import Settings, get_settings
from .logging import setup_logging
from .observability.tracing import setup_tracing
from .runner import failing_tasks

Lifespan = Callable[[FastAPI], AsyncIterator[None]]
StartupHook = Callable[[FastAPI, Settings], Awaitable[None]]
ShutdownHook = Callable[[FastAPI, Settings], Awaitable[None]]


def create_app(
    service_name: str,
    *,
    on_startup: StartupHook | None = None,
    on_shutdown: ShutdownHook | None = None,
) -> FastAPI:
    settings = get_settings()
    settings.service_name = service_name
    setup_logging(settings.log_level)
    setup_tracing(service_name, settings.obs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.ready = False
        if on_startup is not None:
            await on_startup(app, settings)
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            if on_shutdown is not None:
                await on_shutdown(app, settings)

    app = FastAPI(title=service_name, version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["ops"])
    async def health(response: Response) -> dict[str, object]:
        """Liveness + readiness probe.

        Repond 503 des qu'une tache periodique a echoue `UNHEALTHY_AFTER` fois
        de suite. Sans cela un collector ratant tous ses cycles reste `healthy`
        pour Docker, ce qui s'est produit pendant 28 heures.
        """
        failing = failing_tasks()
        body: dict[str, object] = {
            "service": service_name,
            "status": "degraded" if failing else "ok",
            "ready": bool(getattr(app.state, "ready", False)),
        }
        if failing:
            response.status_code = 503
            body["failing_tasks"] = {
                name: {
                    "consecutive_failures": state.consecutive_failures,
                    "last_error": state.last_error,
                    "last_success": state.last_success,
                }
                for name, state in failing.items()
            }
        return body

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
