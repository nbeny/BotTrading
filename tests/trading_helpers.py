"""Load the trading-engine `app` package under a unique name for tests.

Each service names its package `app`, so we register it as `tengine` here to avoid
collisions and to make relative imports inside the package resolve.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_APP_DIR = (
    Path(__file__).resolve().parents[1]
    / "services" / "trading-engine" / "app"
)
_PKG = "tengine"
# Dependency order: leaf modules first, composers last.
_MODULES = ["config", "symbols", "sizing", "guards", "kraken", "engine", "reconcile"]


def load_app() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_APP_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg
    for name in _MODULES:
        path = _APP_DIR / f"{name}.py"
        if not path.exists():
            continue  # module not created yet (early tasks)
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = module
        spec.loader.exec_module(module)
    return pkg


def load_module(name: str) -> types.ModuleType:
    """Load a single app module (and its already-created deps)."""
    load_app()
    return sys.modules[f"{_PKG}.{name}"]
