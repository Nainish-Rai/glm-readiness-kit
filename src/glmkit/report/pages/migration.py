"""Migration tab: the latest eval run. Paired verdict per task type, cost vs quality per target and effort level,
item-level drill-down with side-by-side outputs."""

from __future__ import annotations

from typing import Any

from glmkit.report.registry import Page, RunRef, register_page

MAX_OUTPUT_CHARS = 1500


def _slim_replay(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    out["output"] = (r.get("output") or "")[:MAX_OUTPUT_CHARS]
    return out


@register_page
class MigrationPage(Page):
    id = "migration"
    title = "Migration"
    order = 40
    script = "migration.js"
    css = "migration.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        evals = [r for r in runs if r.envelope.component == "eval" and r.envelope.data.get("comparisons") is not None]
        if not evals:
            return None
        ref = evals[0]  # newest first
        env = ref.envelope
        d = env.data
        cands = [t["label"] for t in d["targets"] if t["label"] != d["baseline"]]
        items: dict[str, dict[str, Any]] = {}
        for it in env.items:
            gid = it.group or it.id
            row = items.setdefault(
                gid,
                {
                    "id": gid,
                    "task_type": it.category,
                    "critical": it.data.get("critical", False),
                    "judged": it.data.get("judged", False),
                    "targets": {},
                },
            )
            row["targets"][it.target or ""] = {
                "status": it.status,
                "score": it.metrics.get("score"),
                "delta": it.metrics.get("delta_vs_baseline"),
                "cost_usd": it.metrics.get("cost_usd"),
                "ms": it.metrics.get("total_ms"),
                "reason": it.reason,
                "replays": [_slim_replay(r) for r in it.data.get("replays", [])],
            }
            row["prompt"] = it.data.get("prompt")
            row["expected"] = it.data.get("expected")
        data: dict[str, Any] = {
            "count": len(items),
            "run": {
                "run_id": env.run_id,
                "started_at": env.started_at.isoformat(),
                "verdict": env.verdict,
                "judge": env.config.get("judge"),
                "seed": env.config.get("seed"),
                "n_boot": env.config.get("n_boot"),
            },
            "other_runs": [
                {
                    "run_id": r.envelope.run_id,
                    "verdict": r.envelope.verdict,
                    "started_at": r.envelope.started_at.isoformat(),
                }
                for r in evals[1:6]
            ],
            "baseline": d["baseline"],
            "margin": d["margin"],
            "candidates": cands,
            "targets": d["targets"],
            "set": d["set"],
            "comparisons": d["comparisons"],
            "target_summary": d["target_summary"],
            "cost_quality": d["cost_quality"],
            "regressions": d["regressions"],
            "judge": d["judge"],
            "budget": d["budget"],
            "warnings": d["warnings"],
            "method": d["method"],
            "price_table": {"version": d["price_table"]["version"], "entries": d["price_table"]["entries"]},
            "items": sorted(items.values(), key=lambda r: r["id"]),
        }
        return data
