"""Evidence index: the runs in ``results/`` that the pack links to, by run id."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from glmkit.results import load_envelopes

# Components whose runs can back a checklist claim. "evidence" (earlier packs) is never evidence for itself.
EVIDENCE_COMPONENTS = ("conform", "bridge", "eval", "guard", "deploy", "forensics", "importers")


@dataclass
class IndexedRun:
    run_id: str
    component: str
    verdict: str
    started_at: str
    path: str  # result.json relative to the results folder
    targets: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    linked_items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_index(results: Path | None) -> tuple[list[IndexedRun], list[str]]:
    """Every non-evidence run under ``results`` (newest first) plus warnings."""
    if results is None or not results.exists():
        return [], ([f"{results} does not exist; the pack has no linked runs"] if results else [])
    found, warnings = load_envelopes(results)
    base = results if results.is_dir() else results.parent
    runs = []
    for path, env in found:
        if env.component == "evidence":
            continue
        runs.append(
            IndexedRun(
                run_id=env.run_id,
                component=env.component,
                verdict=env.verdict,
                started_at=env.started_at.isoformat(),
                path=path.relative_to(base).as_posix(),
                targets=[t.profile for t in env.targets],
                models=sorted({t.model for t in env.targets}),
                total=env.summary.total,
                passed=env.summary.passed,
                failed=env.summary.failed + env.summary.errored,
            )
        )
    return runs, warnings


def runs_for(runs: list[IndexedRun], components: tuple[str, ...], limit: int = 3) -> list[IndexedRun]:
    return [r for r in runs if r.component in components][:limit]
