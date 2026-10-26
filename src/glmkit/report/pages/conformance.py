"""Conformance tab: case x target matrix (latest run per cell), click a cell for its evidence."""

from __future__ import annotations

from typing import Any

from glmkit.report.registry import Page, RunRef, register_page

SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def column_key(ref: RunRef) -> str:
    """Target profile, suffixed with planted mock defects so clean and defective runs get their own column."""
    env = ref.envelope
    name = env.targets[0].profile if env.targets else "unknown"
    defects = env.config.get("mock_defects") or []
    return name + ("+" + "+".join(defects) if defects else "")


@register_page
class ConformancePage(Page):
    id = "conformance"
    title = "Conformance"
    order = 20
    script = "conformance.js"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        conform = [r for r in runs if r.envelope.component == "conform"]  # newest first
        columns: list[dict[str, Any]] = []
        rows: dict[str, dict[str, Any]] = {}
        items: dict[str, Any] = {}
        for ref in conform:
            env = ref.envelope
            col = column_key(ref)
            if col not in {c["key"] for c in columns}:
                t = env.targets[0] if env.targets else None
                columns.append(
                    {
                        "key": col,
                        "profile": t.profile if t else None,
                        "protocol": t.protocol if t else None,
                        "endpoint_class": t.endpoint_class if t else None,
                        "model": t.model if t else None,
                        "defects": env.config.get("mock_defects") or [],
                        "run_id": env.run_id,
                    }
                )
            run_meta = {
                "run_id": env.run_id,
                "started_at": env.started_at.isoformat(),
                "target": col,
                "defects": env.config.get("mock_defects") or [],
            }
            for it in env.items:
                row = rows.setdefault(
                    it.id,
                    {
                        "id": it.id,
                        "group": it.group or it.id,
                        "title": it.title,
                        "category": it.category,
                        "severity": it.severity,
                        "cells": {},
                    },
                )
                if col in row["cells"]:
                    continue  # an older run of the same cell
                ref_id = f"{env.run_id}::{it.id}"
                row["cells"][col] = {"status": it.status, "ref": ref_id, "reason": it.reason}
                items[ref_id] = {"item": it.model_dump(mode="json"), "run": run_meta}
        cells = [c for r in rows.values() for c in r["cells"].values()]
        scored = [c for c in cells if c["status"] not in ("skip", "throttled")]
        passed = sum(1 for c in scored if c["status"] == "pass")
        failed = sum(1 for c in cells if c["status"] in ("fail", "error"))
        hard = sum(
            1
            for r in rows.values()
            for c in r["cells"].values()
            if c["status"] == "fail" and r["severity"] == "critical"
        )
        ordered = sorted(rows.values(), key=lambda r: (SEVERITY_RANK.get(r["severity"] or "", 9), r["id"]))
        return {
            "count": len({r["group"] for r in rows.values()}),
            "columns": columns,
            "rows": ordered,
            "items": items,
            "kpis": [
                {
                    "label": "Cases",
                    "value": len({r["group"] for r in rows.values()}),
                    "sub": f"{len(rows)} case × mode rows",
                },
                {"label": "Targets", "value": len(columns), "sub": "profile (+ planted defects)"},
                {
                    "label": "Pass rate",
                    "value": (passed / len(scored)) if scored else None,
                    "format": "pct",
                    "sub": f"{passed} of {len(scored)} scored cells",
                    "tone": "warn" if scored and passed < len(scored) else None,
                },
                {"label": "Failures", "value": failed, "sub": "fail or error cells", "tone": "err" if failed else None},
                {
                    "label": "Hard-gate failures",
                    "value": hard,
                    "sub": "critical cases failed",
                    "tone": "err" if hard else None,
                },
            ],
        }
