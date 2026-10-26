"""Cost tab: where tokens went, per-turn cache hits with break markers, cost projection (newest cost run)."""

from __future__ import annotations

from typing import Any

from glmkit.report.registry import Page, RunRef, register_page


@register_page
class CostPage(Page):
    id = "cost"
    title = "Cost"
    order = 60
    script = "cost.js"
    css = "cost.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        cost = [r for r in runs if r.envelope.component == "cost" and r.envelope.data.get("totals")]
        if not cost:
            return None
        env = cost[0].envelope  # newest first
        d = env.data
        t = d["totals"]
        turns = {i.id: i.data.get("turns", []) for i in env.items}
        proj = d["projection"]
        ref = proj["reference"]
        return {
            "count": t["breaks"],
            "run": {"run_id": env.run_id, "started_at": env.started_at.isoformat(), "path": cost[0].path},
            "totals": t,
            "options": d["options"],
            "causes": d["causes"],
            "traces": d["traces"],
            "turns": turns,
            "token_buckets": d["token_buckets"],
            "input_composition": d["input_composition"],
            "verbosity": d["verbosity"],
            "tokenizer": d["tokenizer"],
            "projection": proj,
            "kpis": [
                {
                    "label": "Cache hit",
                    "value": t["cache_share"],
                    "format": "pct",
                    "sub": f"{t['calls']} calls in {t['traces']} traces",
                    "tone": "warn" if t["breaks"] else None,
                },
                {
                    "label": "Cache breaks",
                    "value": t["breaks"],
                    "sub": f"in {t['traces_with_breaks']} of {t['traces']} traces",
                    "tone": "err" if t["breaks"] else None,
                },
                {
                    "label": "Cached tokens lost",
                    "value": t["lost_cache_tokens"],
                    "format": "k",
                    "sub": "re-billed as fresh input · heuristic",
                },
                {
                    "label": f"On {ref['host']} {ref['model']}",
                    "value": ref["usd_per_task"],
                    "format": "usd",
                    "sub": "per task at list price",
                },
            ],
        }
