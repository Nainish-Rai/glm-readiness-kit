"""Runs tab: every run from every component, its target and verdict."""

from __future__ import annotations

from collections import Counter
from typing import Any

from glmkit.report.registry import Page, RunRef, register_page


def run_row(ref: RunRef) -> dict[str, Any]:
    env = ref.envelope
    t = env.targets[0] if env.targets else None
    dur = (env.finished_at - env.started_at).total_seconds() if env.finished_at else None
    return {
        "id": env.run_id,
        "run_id": env.run_id,
        "path": ref.path,
        "component": env.component,
        "started_at": env.started_at.isoformat(),
        "duration_s": dur,
        "kit_version": env.kit_version,
        "verdict": env.verdict,
        "summary": env.summary.model_dump(),
        "target": t.model_dump(mode="json") if t else None,
        "targets": [x.profile for x in env.targets],
        "defects": env.config.get("mock_defects") or [],
        "config": env.config,
        "environment": env.environment,
        "artifacts": [a.model_dump() for a in env.artifacts],
        "items": [{"id": i.id, "status": i.status, "severity": i.severity, "reason": i.reason} for i in env.items],
    }


@register_page
class RunsPage(Page):
    id = "runs"
    title = "Runs"
    order = 10
    script = "runs.js"

    def collect(self, runs: list[RunRef]) -> dict[str, Any]:
        rows = [run_row(r) for r in runs]
        verdicts = Counter(r["verdict"] for r in rows)
        components = Counter(r["component"] for r in rows)
        failed_items = sum(r["summary"]["failed"] + r["summary"]["errored"] for r in rows)
        hard = sum(r["summary"]["hard_gate_failures"] for r in rows)
        passing = verdicts.get("pass", 0)
        return {
            "count": len(rows),
            "rows": rows,
            "components": [
                {
                    "name": k,
                    "runs": v,
                    "failing": sum(1 for r in rows if r["component"] == k and r["verdict"] in ("fail", "error")),
                }
                for k, v in components.most_common()
            ],
            "kpis": [
                {"label": "Runs", "value": len(rows), "sub": f"{len(components)} component(s)"},
                {
                    "label": "Passing runs",
                    "value": (passing / len(rows)) if rows else None,
                    "format": "pct",
                    "sub": f"{passing} of {len(rows)} runs pass",
                },
                {
                    "label": "Failing runs",
                    "value": verdicts.get("fail", 0) + verdicts.get("error", 0),
                    "sub": "verdict fail or error",
                    "tone": "err" if verdicts.get("fail", 0) + verdicts.get("error", 0) else None,
                },
                {
                    "label": "Failed items",
                    "value": failed_items,
                    "sub": "failed or errored results",
                    "tone": "err" if failed_items else None,
                },
                {
                    "label": "Hard-gate failures",
                    "value": hard,
                    "sub": "critical items failed",
                    "tone": "err" if hard else None,
                },
            ],
        }
