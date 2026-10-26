"""Dashboard tabs ("pages"). One module per tab in ``glmkit/report/pages/`` (or a plugin module).

A page is a Python class that turns result envelopes into a JSON-serialisable data dict, plus a JavaScript
file that renders that data into the tab's panel with Observatory components. The builder discovers pages,
writes ``data/<id>.js`` per page and inlines each page script into ``index.html``; it never names a page.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from glmkit.contracts.envelope import ResultEnvelope
from glmkit.plugins import import_submodules, load_plugins


@dataclass
class RunRef:
    envelope: ResultEnvelope
    path: str  # result.json path relative to the results folder


class Page:
    id: ClassVar[str]
    title: ClassVar[str]
    order: ClassVar[int] = 100  # tab position, ascending
    script: ClassVar[str]  # JS file name next to the module, e.g. "runs.js"
    css: ClassVar[str | None] = None  # optional CSS file next to the module (token-checked, inlined)

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        """Data for this tab, or None to hide the tab. Put KPI tiles under ``"kpis"`` (see docs/contracts.md);
        put the tab's count badge under ``"count"``."""
        raise NotImplementedError

    def asset_path(self, name: str) -> Path:
        return Path(inspect.getfile(type(self))).with_name(name)


_REGISTRY: dict[str, Page] = {}
_discovered = False


def register_page(cls: type[Page]) -> type[Page]:
    if cls.id in _REGISTRY and type(_REGISTRY[cls.id]) is not cls:
        raise ValueError(f"page {cls.id!r} registered twice")
    _REGISTRY[cls.id] = cls()
    return cls


def discover() -> list[Page]:
    global _discovered
    if not _discovered:
        import_submodules("glmkit.report.pages")
        _discovered = True
    load_plugins()
    return sorted(_REGISTRY.values(), key=lambda p: (p.order, p.id))


def unregister_page(page_id: str) -> None:
    """For tests that register temporary pages."""
    _REGISTRY.pop(page_id, None)
