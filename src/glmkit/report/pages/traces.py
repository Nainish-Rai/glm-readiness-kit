"""Traces tab: imported trace sets (``glmkit traces import``), replayable share, task-type mix, token totals."""

from __future__ import annotations

from collections import Counter
from typing import Any

from glmkit.report.registry import Page, RunRef, register_page

TOKEN_KEYS = (
    "input_tokens_total",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens_total",
    "reasoning_tokens",
)


@register_page
class TracesPage(Page):
    id = "traces"
    title = "Traces"
    order = 60
    script = "traces.js"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        refs = [r for r in runs if r.envelope.component == "traces" and "stats" in r.envelope.data]
        if not refs:
            return None
        sets = []
        tasks: Counter[str] = Counter()
        tokens: dict[str, dict[str, int]] = {}
        total = replayable = baseline = 0
        for ref in refs:
            env, d = ref.envelope, ref.envelope.data
            st = d["stats"]
            sets.append(
                {
                    "id": env.run_id,
                    "name": d.get("set"),
                    "source": d.get("source"),
                    "started_at": env.started_at.isoformat(),
                    "path": ref.path,
                    "output": d.get("output"),
                    "total": st["total"],
                    "replayable": st["replayable"],
                    "baseline_only": st["baseline_only"],
                    "redacted": st["redacted"],
                    "tokens": st["tokens_total"],
                    "cost_usd_reported": st.get("cost_usd_reported"),
                    "by_task_type": st["by_task_type"],
                    "skipped": d.get("skipped") or {},
                    "redactions": d.get("redactions") or {},
                }
            )
            total += st["total"]
            replayable += st["replayable"]
            baseline += st["baseline_only"]
            tasks.update(st["by_task_type"])
            for src, b in st["tokens_by_source"].items():
                acc = tokens.setdefault(src, dict.fromkeys(TOKEN_KEYS, 0))
                for k in TOKEN_KEYS:
                    acc[k] += b.get(k, 0)
        in_total = sum(b["input_tokens_total"] for b in tokens.values())
        return {
            "count": len(sets),
            "sets": sets,
            "totals": {"total": total, "replayable": replayable, "baseline_only": baseline},
            "task_types": [{"name": k, "count": v} for k, v in tasks.most_common()],
            "tokens_by_source": [{"source": k, **v} for k, v in sorted(tokens.items())],
            "kpis": [
                {"label": "Imported sets", "value": len(sets), "sub": f"{len(tokens)} source kind(s)"},
                {"label": "Traces", "value": total, "sub": "one per model call"},
                {
                    "label": "Replayable",
                    "value": (replayable / total) if total else None,
                    "format": "pct",
                    "sub": f"{replayable} of {total} carry prompt content",
                    "tone": "warn" if total and replayable < total else None,
                },
                {"label": "Input tokens", "value": in_total, "format": "k", "sub": "total, cache included"},
            ],
        }
