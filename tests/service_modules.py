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


def _load_package(name: str, directory: Path) -> None:
    """Register ``directory`` as the package ``name``, if it is not already."""
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)]
    )
    assert spec and spec.loader
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[name] = pkg
    spec.loader.exec_module(pkg)


def load_service_module(service: str, module: str) -> ModuleType:
    """Import ``services/<service>/app/<module>.py`` as ``<alias>.<module>``.

    ``module`` may be dotted (``"domain.mapper"``); each intervening directory is
    registered as a package first, so a relative import inside the module
    resolves against its real parent rather than failing or — worse — binding to
    a same-named package from another service.

    The alias is derived from ``service`` rather than passed in, so uniqueness
    holds by construction. A caller-supplied alias could collide silently: the
    module would load under another service's ``__path__``, or the cache would
    hand back the wrong service's module — with no error either way.
    """
    alias = service.replace("-", "_") + "_app"
    app_dir = _REPO_ROOT / "services" / service / "app"
    _load_package(alias, app_dir)

    *packages, leaf = module.split(".")
    directory = app_dir
    name = alias
    for part in packages:
        directory = directory / part
        name = f"{name}.{part}"
        _load_package(name, directory)

    qualified = f"{name}.{leaf}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(qualified, directory / f"{leaf}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: dataclasses resolves ClassVar via sys.modules[__module__].
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod
