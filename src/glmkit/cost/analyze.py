"""``analyze``: traces in, one JSON-serialisable analysis out (the ``data`` of the cost result envelope)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from glmkit.contracts.trace import SwitchTrace
from glmkit.cost import forensics as fx
from glmkit.cost.prices import DEFAULT_HOST, PriceTable, load_price_table
from glmkit.cost.projection import M, project, workload_from_traces

DEFAULT_MODELS = ["glm-5.3", "glm-5.3-flash"]


class TraceFileError(ValueError):
    pass


def read_traces(path: Path) -> list[SwitchTrace]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(SwitchTrace.model_validate_json(line))
        except ValueError as e:
            raise TraceFileError(f"{path}:{n}: not a SwitchTrace v0 record ({str(e).splitlines()[0]})") from e
    return out


def read_counts(path: Path) -> list[dict[str, Any]]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise TraceFileError(f"{path}:{n}: not JSON") from e
    return rows


def _wasted_usd(lost: int, model: str, table: PriceTable) -> float | None:
    e = table.lookup(model, DEFAULT_HOST)
    if not e or e.input is None or e.cached_input is None:
        return None
    return lost / M * (e.input - e.cached_input)


def analyze(
    traces: Iterable[SwitchTrace],
    *,
    table: PriceTable | None = None,
    counts: list[dict[str, Any]] | None = None,
    models: list[str] | None = None,
    threshold: float = fx.DEFAULT_BREAK_THRESHOLD,
    idle_ttl_s: float = fx.DEFAULT_IDLE_TTL_S,
    credits_per_usd: float | None = None,
    tasks_per_month: float | None = None,
    disabled: set[str] | None = None,
) -> dict[str, Any]:
    table = table or load_price_table()
    grouped = fx.group_traces(traces)
    ctx = fx.DetectorContext(idle_ttl_s=idle_ttl_s)
    per_trace = [fx.analyze_trace(c, threshold=threshold, disabled=disabled, ctx=ctx) for c in grouped.values()]

    causes: dict[str, dict[str, Any]] = {}
    wasted_usd: float | None = 0.0
    for tr in per_trace:
        for t in tr["turns"]:
            if not t["broke"]:
                continue
            key = t["primary_cause"]
            row = causes.setdefault(
                key,
                {
                    "cause": key,
                    "label": fx.CAUSE_LABELS.get(key, "No cause found in the prompt diff"),
                    "breaks": 0,
                    "lost_tokens": 0,
                    "usd_wasted": 0.0,
                    "traces": [],
                },
            )
            row["breaks"] += 1
            row["lost_tokens"] += t["lost_tokens"] or 0
            w = _wasted_usd(t["lost_tokens"] or 0, t["model"], table)
            if w is None:
                row["usd_wasted"] = None
                wasted_usd = None
            elif row["usd_wasted"] is not None:
                row["usd_wasted"] += w
                wasted_usd = None if wasted_usd is None else wasted_usd + w
            if tr["trace_id"] not in row["traces"]:
                row["traces"].append(tr["trace_id"])
    cause_rows = sorted(causes.values(), key=lambda r: -r["lost_tokens"])

    lost_total = sum(r["lost_tokens"] for r in cause_rows)
    w = workload_from_traces(grouped, lost_cache_tokens=lost_total)
    ms = models or DEFAULT_MODELS
    projection = project(w, table, ms, credits_per_usd=credits_per_usd, tasks_per_month=tasks_per_month)

    buckets: dict[tuple[str, str], int] = defaultdict(int)
    for calls in grouped.values():
        for c in calls:
            u = c.usage
            if u.input_tokens_total is None and u.output_tokens_total is None:
                continue
            cr = u.cache_read_tokens or 0
            out = u.output_tokens_total or 0
            rs = min(out, u.reasoning_tokens or 0)
            m = c.request.model
            buckets[(m, "cached input")] += cr
            buckets[(m, "fresh input")] += max(0, (u.input_tokens_total or 0) - cr)
            buckets[(m, "reasoning output")] += rs
            buckets[(m, "answer output")] += out - rs

    logged = [t for tr in per_trace for t in tr["turns"] if t["cache_read"] is not None and t["input_total"]]
    ti, tc = sum(t["input_total"] for t in logged), sum(t["cache_read"] for t in logged)
    return {
        "totals": {
            "traces": len(grouped),
            "calls": w.calls,
            "calls_without_usage": w.calls_without_usage,
            "input_tokens": w.input_total,
            "cache_read_tokens": w.cache_read,
            "output_tokens": w.output_total,
            "reasoning_tokens": w.reasoning_total,
            "cache_share": (tc / ti) if ti else None,
            "breaks": sum(r["breaks"] for r in cause_rows),
            "traces_with_breaks": sum(1 for t in per_trace if t["breaks"]),
            "lost_cache_tokens": lost_total,
            "wasted_usd": wasted_usd if cause_rows or ti else None,
            "synthetic": all(str(c.source.ref).startswith("synthetic/") for cs in grouped.values() for c in cs)
            and bool(grouped),
        },
        "options": {
            "break_threshold": threshold,
            "idle_ttl_s": idle_ttl_s,
            "disabled_detectors": sorted(fx.disabled_from_env() if disabled is None else disabled),
            "models": ms,
        },
        "causes": cause_rows,
        "cause_labels": fx.CAUSE_LABELS,
        "traces": [{k: v for k, v in tr.items() if k != "turns"} for tr in per_trace],
        "token_buckets": [
            {"model": m, "bucket": b, "tokens": n} for (m, b), n in sorted(buckets.items(), key=lambda kv: -kv[1])
        ],
        "input_composition": fx.input_composition(grouped),
        "verbosity": fx.verbosity(grouped, table),
        "tokenizer": fx.tokenizer_inflation(counts or []),
        "projection": projection,
        "price_table": {"version": table.version, "updated": table.updated.isoformat()},
        "turns": {tr["trace_id"]: tr["turns"] for tr in per_trace},
    }
