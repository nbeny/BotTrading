"""Guard: load service `app` packages via tests/service_modules.py, never bare."""

import sys


def pytest_collection_finish(session):
    leaked = sorted(k for k in sys.modules if k == "app" or k.startswith("app."))
    assert not leaked, (
        f"{leaked} loaded under the bare name 'app'. Every service ships a package "
        "named 'app'; use tests/service_modules.load_service_module() instead."
    )
