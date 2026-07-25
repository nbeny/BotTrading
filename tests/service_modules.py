"""Load a service's ``app`` package under a unique top-level alias.

Every service in this repo ships a package literally named ``app``. Loading two
of them in one pytest session makes the second shadow the first in
``sys.modules``, so a relative import such as ``from .features import
FeatureStore`` resolves against the wrong service and raises ModuleNotFoundError
— which aborted collection of the whole suite.

Registering each service under a distinct alias (``ai_worker_haiku_app``,
``api_gateway_app``, …) gives every module a parent package rooted at its own
service directory, so relative imports resolve within that service and nowhere
else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_service_module(service: str, module: str) -> ModuleType:
    """Import ``services/<service>/app/<module>.py`` as ``<alias>.<module>``.

    The alias is derived from ``service`` rather than passed in, so uniqueness
    holds by construction. A caller-supplied alias could collide silently: the
    module would load under another service's ``__path__``, or the cache would
    hand back the wrong service's module — with no error either way.
    """
    alias = service.replace("-", "_") + "_app"
    app_dir = _REPO_ROOT / "services" / service / "app"

    if alias not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            alias,
            app_dir / "__init__.py",
            submodule_search_locations=[str(app_dir)],
        )
        assert pkg_spec and pkg_spec.loader
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[alias] = pkg
        pkg_spec.loader.exec_module(pkg)

    qualified = f"{alias}.{module}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(qualified, app_dir / f"{module}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: dataclasses resolves ClassVar via sys.modules[__module__].
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod
