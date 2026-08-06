"""Guard: load service `app` packages via tests/service_modules.py, never bare."""

import sys

import pytest


def pytest_collection_finish(session):
    leaked = sorted(k for k in sys.modules if k == "app" or k.startswith("app."))
    assert not leaked, (
        f"{leaked} loaded under the bare name 'app'. Every service ships a package "
        "named 'app'; use tests/service_modules.load_service_module() instead."
    )


@pytest.fixture(autouse=True)
def _clear_pipeline_stage_cache():
    """Empty the pipeline aggregate cache around every test.

    `load_service_module` caches by `sys.modules`, so `systems_pipeline.STAGE_CACHE`
    is one singleton shared by the whole session. Within its 30s TTL an entry left
    by one test silently satisfies another test's request — which then passes
    without ever reaching the code it claims to exercise. Reproduced during the T7
    review: a test given a deliberately broken fake session still went green,
    served entirely by a neighbour's leftover entry.

    Imported lazily so this module stays importable when a service package is not
    loadable in some future test layout.
    """
    try:
        from service_modules import load_service_module

        cache = load_service_module("api-gateway", "systems_pipeline").STAGE_CACHE
        regime_api = load_service_module("api-gateway", "regime_api")
        journal_api = load_service_module("api-gateway", "journal_api")
    except Exception:  # pragma: no cover - the guard must never break collection
        yield
        return
    cache.clear()
    regime_api.REGIME_CACHE.clear()
    journal_api.JUDGED_CACHE.clear()
    yield
    cache.clear()
    regime_api.REGIME_CACHE.clear()
    journal_api.JUDGED_CACHE.clear()
