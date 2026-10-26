"""Discover, load and select conformance cases.

Case roots (``glmkit.paths.default_case_roots``) hold ``<category>/<ID>-slug.yaml`` files. A ``fixtures/``
folder at a root holds shared JSON/YAML pulled in with ``{$ref: fixtures/...}`` anywhere in a case. Adding a
case folder needs no code change: every ``*.yaml`` under a root (outside ``fixtures/``) is a case.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from glmkit.contracts.case import ConformanceCase
from glmkit.paths import default_case_roots

FIXTURES_DIR = "fixtures"


class CaseError(ValueError):
    pass


@dataclass
class LoadedCase:
    case: ConformanceCase
    path: Path
    root: Path
    sha256: str

    @property
    def rel(self) -> str:
        return self.path.relative_to(self.root).as_posix()


def _load_data(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def resolve_refs(obj: Any, root: Path, _depth: int = 0) -> Any:
    """Replace ``{"$ref": "relative/path.json"}`` (relative to the case root) with the file's content."""
    if _depth > 20:
        raise CaseError("$ref nesting too deep")
    if isinstance(obj, dict):
        if set(obj) == {"$ref"} and isinstance(obj["$ref"], str) and not obj["$ref"].startswith("#"):
            target = (root / obj["$ref"]).resolve()
            if root.resolve() not in target.parents:
                raise CaseError(f"$ref {obj['$ref']!r} escapes the case root")
            if not target.is_file():
                raise CaseError(f"$ref {obj['$ref']!r} not found under {root}")
            return resolve_refs(_load_data(target), root, _depth + 1)
        return {k: resolve_refs(v, root, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_refs(v, root, _depth + 1) for v in obj]
    return obj


def case_files(roots: list[Path]) -> list[tuple[Path, Path]]:
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.yaml")):
            rel = p.relative_to(root)
            if rel.parts[0] == FIXTURES_DIR or p.name.startswith("_"):
                continue
            out.append((root, p))
    return out


def load_case(path: Path, root: Path) -> LoadedCase:
    raw = path.read_bytes()
    try:
        data = resolve_refs(yaml.safe_load(raw), root)
        case = ConformanceCase.model_validate(data)
    except (CaseError, ValueError, yaml.YAMLError) as e:
        raise CaseError(f"{path}: {e}") from e
    if not path.stem.startswith(case.id):
        raise CaseError(f"{path}: file name must start with the case id {case.id!r}")
    return LoadedCase(case=case, path=path, root=root, sha256=hashlib.sha256(raw).hexdigest())


def matches(lc: LoadedCase, patterns: list[str]) -> bool:
    keys = [lc.case.id, lc.case.category, lc.rel, lc.path.stem, *lc.case.tags]
    return any(fnmatch.fnmatchcase(k, p) for p in patterns for k in keys)


def load_cases(
    roots: list[Path] | None = None, patterns: list[str] | None = None, *, include_opt_in: bool = False
) -> list[LoadedCase]:
    """Load cases from ``roots`` (default case roots plus nothing else), filtered by glob ``patterns`` matched
    against id, category, relative path, file stem and tags. Opt-in cases only load when a pattern names
    them or ``include_opt_in`` is set."""
    roots = roots if roots is not None else default_case_roots()
    seen: dict[str, LoadedCase] = {}
    for root, path in case_files(roots):
        lc = load_case(path, root)
        if lc.case.id in seen:
            raise CaseError(f"duplicate case id {lc.case.id}: {seen[lc.case.id].path} and {path}")
        seen[lc.case.id] = lc
    out = []
    for lc in seen.values():
        if patterns and not matches(lc, patterns):
            continue
        if lc.case.opt_in and not include_opt_in and not (patterns and any(lc.case.id == p for p in patterns)):
            continue
        out.append(lc)
    return sorted(out, key=lambda lc: lc.case.id)
