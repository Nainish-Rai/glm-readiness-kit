"""Adapter registry. A new source is one file in ``glmkit/traces/adapters/`` (or a plugin module) that
subclasses ``Adapter`` and is decorated with ``@register_adapter``."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import yaml

from glmkit.contracts.trace import SwitchTrace
from glmkit.plugins import import_submodules, load_plugins

UNLABELLED = "unlabelled"


class TaskMapper:
    """Assigns ``task_type``. Order: explicit metadata key, tag map, model map, the first source tag
    (``use_first_tag: false`` turns that off), default ``unlabelled``.

    Mapping file (YAML or JSON)::

        metadata_keys: [task_type, task]        # metadata keys read as the task type (default: those two)
        tags: {billing-bot: extraction}         # source tag -> task type
        models: {"^gpt-4o-mini": classification}  # regex on the model name -> task type
        default: unlabelled
    """

    def __init__(self, spec: dict[str, Any] | None = None) -> None:
        spec = spec or {}
        self.metadata_keys: list[str] = list(spec.get("metadata_keys") or ["task_type", "task"])
        self.tags: dict[str, str] = dict(spec.get("tags") or {})
        self.models: list[tuple[re.Pattern[str], str]] = [
            (re.compile(k), v) for k, v in (spec.get("models") or {}).items()
        ]
        self.default: str = spec.get("default") or UNLABELLED
        self.use_first_tag: bool = bool(spec.get("use_first_tag", True))

    @classmethod
    def from_file(cls, path: Path | None) -> TaskMapper:
        if path is None:
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # YAML is a superset of JSON
        if not isinstance(data, dict):
            raise ValueError(f"{path}: mapping file must be a YAML/JSON object")
        return cls(data)

    def assign(self, *, tags: list[str] | None = None, meta: dict[str, Any] | None = None, model: str = "") -> str:
        for k in self.metadata_keys:
            v = (meta or {}).get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for t in tags or []:
            if t in self.tags:
                return self.tags[t]
        for rx, task in self.models:
            if rx.search(model or ""):
                return task
        if tags and self.use_first_tag:
            return tags[0]
        return self.default


@dataclass
class ImportContext:
    """Passed to every adapter: task mapping, file reference helper, and counters for fail-soft skips."""

    path: Path
    mapper: TaskMapper = field(default_factory=TaskMapper)
    skipped: Counter[str] = field(default_factory=Counter)

    def ref(self, row: int | str) -> str:
        return f"{self.path.name}#{row}"

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1

    def task_type(self, *, tags: list[str] | None = None, meta: dict[str, Any] | None = None, model: str = "") -> str:
        return self.mapper.assign(tags=tags, meta=meta, model=model)


class Adapter:
    kind: ClassVar[str]  # value of SwitchTrace.source.kind
    version: ClassVar[str] = "0.1"
    title: ClassVar[str] = ""
    formats: ClassVar[str] = "jsonl"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        raise NotImplementedError


_REGISTRY: dict[str, Adapter] = {}
_discovered = False


def register_adapter(cls: type[Adapter]) -> type[Adapter]:
    if cls.kind in _REGISTRY and type(_REGISTRY[cls.kind]) is not cls:
        raise ValueError(f"trace adapter {cls.kind!r} registered twice")
    _REGISTRY[cls.kind] = cls()
    return cls


def adapters() -> dict[str, Adapter]:
    global _discovered
    if not _discovered:
        import_submodules("glmkit.traces.adapters")
        _discovered = True
    load_plugins()
    return dict(sorted(_REGISTRY.items()))


def get_adapter(kind: str) -> Adapter:
    reg = adapters()
    if kind not in reg:
        raise KeyError(f"unknown source kind {kind!r}; available: {', '.join(reg)}")
    return reg[kind]


# ------------------------------------------------------------------ shared readers


def read_records(ctx: ImportContext) -> Iterator[tuple[int, Any]]:
    """Yield ``(row_number, object)`` from JSONL, a JSON array/object, or CSV (JSON-looking cells are parsed).
    Bad lines are counted in ``ctx.skipped`` instead of raising."""
    path = ctx.path
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    if suffix == ".csv":
        for i, row in enumerate(csv.DictReader(text.splitlines()), start=1):
            yield i, {k: parse_json_cell(v) for k, v in row.items() if k}
        return
    stripped = text.lstrip()
    if suffix == ".json" or (stripped.startswith("[") and suffix != ".jsonl"):
        try:
            data = json.loads(text)
        except ValueError:
            ctx.skip("invalid JSON file")
            return
        rows = data if isinstance(data, list) else [data]
        yield from enumerate(rows, start=1)
        return
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            yield i, json.loads(line)
        except ValueError:
            ctx.skip("invalid JSON line")


def parse_json_cell(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("{", "[") and v[-1:] in ("}", "]"):
        try:
            return json.loads(v)
        except ValueError:
            return v
    if v == "":
        return None
    return v


def as_int(v: Any) -> int | None:
    try:
        return None if v is None or v == "" else int(float(v))
    except (TypeError, ValueError):
        return None


def as_float(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None
