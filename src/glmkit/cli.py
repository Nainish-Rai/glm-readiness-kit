"""Root ``glmkit`` CLI. Sub-apps are discovered, never listed here.

Registration contract (see docs/contracts.md, "CLI sub-app"):
* in-tree: a module ``glmkit/<area>/cli.py`` that defines ``app: typer.Typer`` is mounted as
  ``glmkit <area>`` (override the name with ``COMMAND = "..."``). It may also define
  ``register_cli(root: typer.Typer)`` to add top-level commands (e.g. ``glmkit init``).
* out-of-tree: a plugin module (``GLMKIT_PLUGINS`` / entry point group ``glmkit.plugins``) that defines
  ``register_cli(root)``.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from types import ModuleType

import typer

import glmkit
from glmkit.plugins import load_plugins


def discover_cli_modules() -> list[ModuleType]:
    mods = []
    for info in sorted(pkgutil.iter_modules(glmkit.__path__), key=lambda i: i.name):
        if not info.ispkg or info.name.startswith("_"):
            continue
        name = f"glmkit.{info.name}.cli"
        if importlib.util.find_spec(name) is None:
            continue
        try:
            mods.append(importlib.import_module(name))
        except Exception as e:  # one broken slice must not take the whole CLI down
            print(f"glmkit: failed to load {name}: {e}", file=sys.stderr)
    return mods


def build_app() -> typer.Typer:
    root = typer.Typer(
        name="glmkit",
        help="GLM Production Readiness Kit: conformance, bridge, eval, guard, deploy and evidence.",
        no_args_is_help=True,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    def _version(value: bool) -> None:
        if value:
            typer.echo(glmkit.__version__)
            raise typer.Exit()

    @root.callback()
    def _root(
        version: bool = typer.Option(
            False, "--version", help="Print the kit version and exit", is_eager=True, callback=_version
        ),
    ) -> None:
        """GLM Production Readiness Kit."""

    mounted: list[tuple[str, typer.Typer, str | None]] = []
    for mod in discover_cli_modules():
        sub = getattr(mod, "app", None)
        if isinstance(sub, typer.Typer):
            area = mod.__name__.split(".")[1]
            mounted.append((getattr(mod, "COMMAND", area), sub, getattr(mod, "HELP", None) or sub.info.help))
        if callable(getattr(mod, "register_cli", None)):
            mod.register_cli(root)
    try:
        plugins = load_plugins()
    except Exception as e:
        print(f"glmkit: failed to load plugins: {e}", file=sys.stderr)
        plugins = []
    for mod in plugins:
        if callable(getattr(mod, "register_cli", None)):
            mod.register_cli(root)
    for name, sub, help_ in sorted(mounted, key=lambda m: m[0]):
        root.add_typer(sub, name=name, help=help_)
    return root


def main() -> None:
    build_app()()
