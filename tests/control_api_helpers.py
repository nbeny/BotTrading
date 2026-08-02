"""Load the control-api `app` package under a unique name for tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "services" / "control-api" / "app"
_PKG = "capi"
_MODULES = ["commands", "state", "auth_dep", "turnstile"]
_ROUTERS = ["auth", "settings", "positions", "opportunities", "orders", "collectors"]


def load_app() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_APP_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg
    routers_dir = _APP_DIR / "routers"
    if routers_dir.exists():
        rpkg = types.ModuleType(f"{_PKG}.routers")
        rpkg.__path__ = [str(routers_dir)]  # type: ignore[attr-defined]
        sys.modules[f"{_PKG}.routers"] = rpkg
    for name in _MODULES:
        _load_one(name, _APP_DIR / f"{name}.py")
    for name in _ROUTERS:
        _load_one(f"routers.{name}", routers_dir / f"{name}.py")
    return pkg


def _load_one(dotted: str, path: Path) -> None:
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{dotted}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{dotted}"] = module
    spec.loader.exec_module(module)


def load_module(name: str) -> types.ModuleType:
    load_app()
    return sys.modules[f"{_PKG}.{name}"]
