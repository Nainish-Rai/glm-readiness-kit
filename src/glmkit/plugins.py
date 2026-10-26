"""Discovery helpers shared by every registry (CLI sub-apps, checks, executors, defects, report pages).

Built-in extensions are found by importing every module of a known package folder, so a new slice adds a
file and nothing else. Out-of-tree extensions (and tests) name extra modules in the ``GLMKIT_PLUGINS``
environment variable (comma-separated import paths) or the ``glmkit.plugins`` entry-point group; importing
such a module runs its ``register_*`` calls / decorators.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import pkgutil
from importlib.metadata import entry_points
from types import ModuleType

PLUGIN_ENV = "GLMKIT_PLUGINS"
ENTRY_POINT_GROUP = "glmkit.plugins"


def import_submodules(package: str) -> list[ModuleType]:
    """Import every direct submodule of ``package`` (sorted by name) and return them."""
    pkg = importlib.import_module(package)
    mods: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        mods.append(importlib.import_module(f"{package}.{info.name}"))
    return mods


def plugin_module_names() -> list[str]:
    names = [n.strip() for n in os.environ.get(PLUGIN_ENV, "").split(",") if n.strip()]
    with contextlib.suppress(Exception):  # broken package metadata must not break the CLI
        names += [ep.value.split(":")[0] for ep in entry_points(group=ENTRY_POINT_GROUP)]
    return names


def load_plugins() -> list[ModuleType]:
    """Import external plugin modules. Safe to call repeatedly (imports are cached)."""
    return [importlib.import_module(n) for n in plugin_module_names()]
