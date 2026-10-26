"""Scorecard tab: provider x category pass rates with Wilson 95% CIs, hard-gate failures first.

A "provider" is a target profile (plus planted mock defects), the same column rule as the Conformance tab. For
each provider the newest result of every item counts, so separate runs (tool cases, wire cases ...) merge into
one card. Statistical items count every attempt as a trial; other items count once. Soft failures, skips and
throttled items are not scored.
"""

from __future__ import annotations

from typing import Any

from glmkit.conform.stats import wilson_interval
from glmkit.contracts.envelope import ItemResult, compute_verdict
from glmkit.report.registry import Page, RunRef, register_page

SHAPE_GROUP = "TOOL-006"  # schema-shape A/B: the flat variant is the control


def provider_key(ref: RunRef) -> str:
    env = ref.envelope
    name = env.targets[0].profile if env.targets else "unknown"
    defects = env.config.get("mock_defects") or []
    return name + ("+" + "+".join(defects) if defects else "")


def trials_of(item: ItemResult) -> tuple[int, int] | None:
    """(passes, trials) for a scored item, None when it is not scored."""
    if item.status in ("skip", "throttled", "soft_fail"):
        return None
    rate, attempts = item.metrics.get("pass_rate"), item.metrics.get("attempts")
    if isinstance(rate, int | float) and isinstance(attempts, int) and attempts > 1:
        return round(rate * attempts), attempts
    return (1 if item.status == "pass" else 0), 1


def rate_cell(passes: int, trials: int) -> dict[str, Any]:
    lo, hi = wilson_interval(passes, trials)
    return {
        "passes": passes,
        "trials": trials,
        "rate": passes / trials if trials else None,
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
    }


@register_page
class ScorecardPage(Page):
    id = "scorecard"
    title = "Scorecard"
    order = 30
    script = "scorecard.js"
    css = "scorecard.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        conform = [r for r in runs if r.envelope.component == "conform"]  # newest first
        if not conform:
            return None
        providers: dict[str, dict[str, Any]] = {}
        newest: dict[str, dict[str, tuple[ItemResult, dict[str, Any]]]] = {}
        for ref in conform:
            key, env = provider_key(ref), ref.envelope
            t = env.targets[0] if env.targets else None
            defects = env.config.get("mock_defects") or []
            providers.setdefault(
                key,
                {
                    "key": key,
                    "profile": t.profile if t else None,
                    "model": t.model if t else None,
                    "protocol": t.protocol if t else None,
                    "endpoint_class": t.endpoint_class if t else None,
                    "defects": defects,
                    "run_id": env.run_id,
                },
            )
            run_meta = {
                "run_id": env.run_id,
                "started_at": env.started_at.isoformat(),
                "target": key,
                "defects": defects,
            }
            per = newest.setdefault(key, {})
            for it in env.items:
                per.setdefault(it.id, (it, run_meta))  # runs are newest first

        categories = sorted({it.category or "uncategorised" for per in newest.values() for it, _ in per.values()})
        cat_index = {c: i for i, c in enumerate(categories)}
        refs: dict[str, Any] = {}

        def ref_of(item: ItemResult, run: dict[str, Any]) -> str:
            ref = f"{run['run_id']}::{item.id}"
            refs.setdefault(ref, {"item": item.model_dump(mode="json"), "run": run})
            return ref

        rows, hard, shapes = [], [], []
        total_pass = total_trials = critical_ran = critical_ok = 0
        for key, prov in providers.items():
            items = list(newest[key].values())
            cells: dict[str, Any] = {}
            for cat in categories:
                in_cat = [(it, run) for it, run in items if (it.category or "uncategorised") == cat]
                scored = [(it, trials_of(it), run) for it, run in in_cat]
                scored = [(it, tr, run) for it, tr, run in scored if tr is not None]
                if not in_cat:
                    continue
                p, n = sum(tr[0] for _, tr, _ in scored), sum(tr[1] for _, tr, _ in scored)
                worst = min(in_cat, key=lambda ir: (_rank(ir[0].status), ir[0].id))
                cell = {
                    "cases": len({it.group or it.id for it, _ in in_cat}),
                    "items": len(in_cat),
                    "ref": ref_of(*worst),
                    "worst": worst[0].status,
                    "failing": sum(1 for it, _ in in_cat if it.status in ("fail", "error")),
                }
                cell |= (
                    rate_cell(p, n) if n else {"passes": 0, "trials": 0, "rate": None, "ci_low": None, "ci_high": None}
                )
                cells[cat] = cell
            p_all = sum(c["passes"] for c in cells.values())
            n_all = sum(c["trials"] for c in cells.values())
            total_pass, total_trials = total_pass + p_all, total_trials + n_all
            crit = [
                (it, run) for it, run in items if it.severity == "critical" and it.status not in ("skip", "throttled")
            ]
            critical_ran += len(crit)
            critical_ok += sum(1 for it, _ in crit if it.status == "pass")
            failing = [(it, run) for it, run in crit if it.status in ("fail", "error")]
            for it, run in failing:
                hard.append(
                    {
                        "provider": key,
                        "id": it.id,
                        "group": it.group or it.id,
                        "title": it.title,
                        "category": it.category,
                        "reason": it.reason,
                        "ref": ref_of(it, run),
                    }
                )
            verdict = compute_verdict([it for it, _ in items])
            rows.append(
                {
                    **prov,
                    "verdict": verdict,
                    "hard_failures": len(failing),
                    "overall": rate_cell(p_all, n_all),
                    "cells": cells,
                    "items": len(items),
                }
            )
            shape = shape_row(key, items, ref_of)
            if shape:
                shapes.append(shape)

        overall = rate_cell(total_pass, total_trials)
        lo, hi = overall["ci_low"], overall["ci_high"]
        return {
            "count": len(rows),
            "categories": [{"name": c, "index": cat_index[c] + 1} for c in categories],
            "providers": rows,
            "hard_gates": hard,
            "shapes": shapes,
            "items": refs,
            "kpis": [
                {"label": "Providers", "value": len(rows), "sub": "profile (+ planted defects)"},
                {
                    "label": "Hard-gate failures",
                    "value": len(hard),
                    "sub": "critical cases failed",
                    "tone": "err" if hard else None,
                },
                {
                    "label": "Critical cases",
                    "value": critical_ok,
                    "sub": f"of {critical_ran} scored passed",
                    "tone": "err" if critical_ok < critical_ran else None,
                },
                {
                    "label": "Pass rate",
                    "value": overall["rate"],
                    "format": "pct",
                    "sub": f"{total_pass}/{total_trials} trials · 95% CI {lo:.0%} to {hi:.0%}"
                    if overall["rate"] is not None
                    else "nothing scored",
                },
                {"label": "Categories", "value": len(categories), "sub": "scored per provider"},
            ],
        }


def _rank(status: str) -> int:
    return {"error": 0, "fail": 1, "soft_fail": 2, "throttled": 3, "pass": 4, "skip": 5}.get(status, 9)


def shape_row(key: str, items: list[tuple[ItemResult, dict[str, Any]]], ref_of: Any) -> dict[str, Any] | None:
    """TOOL-006 schema-shape A/B: pass rate per shape; sensitivity = flat (control) rate minus shape rate."""
    by: dict[str, list[tuple[ItemResult, dict[str, Any]]]] = {}
    for it, run in items:
        if (it.group or it.id) == SHAPE_GROUP and it.status != "skip":
            by.setdefault(it.labels.get("variant", "oneof"), []).append((it, run))
    if "flat" not in by:
        return None

    def rate(entries: list[tuple[ItemResult, dict[str, Any]]]) -> float:
        return sum(1 for it, _ in entries if it.status == "pass") / len(entries)

    control = rate(by["flat"])
    out = []
    for name, entries in by.items():
        worst = min(entries, key=lambda e: (_rank(e[0].status), e[0].id))
        out.append(
            {
                "shape": name,
                "rate": rate(entries),
                "n": len(entries),
                "sensitivity": None if name == "flat" else round(control - rate(entries), 4),
                "ref": ref_of(*worst),
            }
        )
    out.sort(key=lambda s: (s["shape"] == "flat", s["shape"]))
    return {"provider": key, "control": control, "shapes": out}
