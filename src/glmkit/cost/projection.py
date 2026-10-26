"""Cost projection: the same workload on Z.ai pay-as-you-go, each priced host and the Coding Plan tiers.

All arithmetic is plain and the assumptions are returned with the numbers (``assumptions``). A price the table
does not have makes the dependent cost ``None`` ("not logged"); where the only missing piece is the cached-input
price, ``usd_upper_bound`` prices cached tokens as fresh input (a heuristic ceiling, never a measurement).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from glmkit.contracts.trace import SwitchTrace
from glmkit.cost.prices import DEFAULT_HOST, PriceEntry, PriceTable

M = 1_000_000
WEEKS_PER_MONTH = 52 / 12


@dataclass
class Workload:
    tasks: int  # one task = one trace_id
    calls: int
    calls_without_usage: int
    input_total: int  # includes cache reads and writes (OpenAI convention)
    cache_read: int
    cache_write: int
    output_total: int  # includes reasoning tokens
    reasoning_total: int
    lost_cache_tokens: int = 0  # tokens that forensics says a cache break forced to be re-billed as fresh input


def workload_from_traces(grouped: dict[str, list[SwitchTrace]], lost_cache_tokens: int = 0) -> Workload:
    w = Workload(len(grouped), 0, 0, 0, 0, 0, 0, 0, lost_cache_tokens)
    for calls in grouped.values():
        for c in calls:
            w.calls += 1
            u = c.usage
            if u.input_tokens_total is None and u.output_tokens_total is None:
                w.calls_without_usage += 1
                continue
            w.input_total += u.input_tokens_total or 0
            w.cache_read += u.cache_read_tokens or 0
            w.cache_write += u.cache_write_tokens or 0
            w.output_total += u.output_tokens_total or 0
            w.reasoning_total += u.reasoning_tokens or 0
    return w


def cost_usd(w: Workload, e: PriceEntry, *, cache_read: int | None = None, output: int | None = None) -> dict[str, Any]:
    """Cost of ``w`` under price entry ``e`` (USD per 1M tokens).

    cost = (input_total - cache_read) * input + cache_read * cached_input + output * output
    Cache writes are billed as fresh input (Z.ai lists no write price). ``cache_read`` / ``output`` override the
    workload's, for what-if rows."""
    cr = w.cache_read if cache_read is None else cache_read
    out = w.output_total if output is None else output
    fresh = max(0, w.input_total - cr)
    missing = [n for n, v in (("input", e.input), ("output", e.output)) if v is None]
    if cr and e.cached_input is None:
        missing.append("cached_input")
    fresh_usd = None if e.input is None else fresh / M * e.input
    out_usd = None if e.output is None else out / M * e.output
    cached_usd = 0.0 if not cr else (None if e.cached_input is None else cr / M * e.cached_input)
    parts = {"fresh_input": fresh_usd, "cached_input": cached_usd, "output": out_usd}
    usd = None if any(v is None for v in parts.values()) else sum(parts.values())  # type: ignore[arg-type]
    upper = None
    if usd is None and e.input is not None and e.output is not None:
        upper = (fresh + cr) / M * e.input + out / M * e.output  # cached priced as fresh: ceiling
    return {"usd": usd, "usd_upper_bound": upper, "parts": parts, "missing": missing}


def _per_task(v: float | None, w: Workload) -> float | None:
    return None if v is None or not w.tasks else v / w.tasks


def project(
    w: Workload,
    table: PriceTable,
    models: list[str],
    *,
    credits_per_usd: float | None = None,
    tasks_per_month: float | None = None,
    reference_model: str | None = None,
) -> dict[str, Any]:
    """Projection rows for pay-as-you-go hosts and Coding Plan tiers."""
    rows: list[dict[str, Any]] = []
    for model in models:
        entries = table.hosts(model)
        if not entries:
            rows.append({"host": "not logged", "model": model, "usd": None, "missing": ["price entry"]})
        for e in entries:
            now = cost_usd(w, e)
            fixed = cost_usd(w, e, cache_read=min(w.input_total, w.cache_read + w.lost_cache_tokens))
            rows.append(
                {
                    "host": e.host,
                    "model": model,
                    "prices": {"input": e.input, "cached_input": e.cached_input, "output": e.output},
                    "usd": now["usd"],
                    "usd_upper_bound": now["usd_upper_bound"],
                    "usd_per_task": _per_task(now["usd"], w),
                    "parts": now["parts"],
                    "missing": now["missing"],
                    "usd_if_cache_fixed": fixed["usd"],
                    "source": e.source,
                    "retrieved": e.retrieved.isoformat(),
                    "note": e.note,
                }
            )
    ref_model = reference_model or models[0]
    ref = next((r for r in rows if r["host"] == DEFAULT_HOST and r["model"] == ref_model), None)
    per_task = ref["usd_per_task"] if ref else None
    cp = table.coding_plan
    discounts = {d.period: d.discount for d in cp.billing_discounts}
    tiers = []
    for t in cp.tiers:
        row: dict[str, Any] = {
            "tier": t.name,
            "usd_per_month": t.usd_per_month,
            "usd_per_month_quarterly": t.usd_per_month * (1 - discounts["quarterly"])
            if "quarterly" in discounts
            else None,
            "usd_per_month_yearly": t.usd_per_month * (1 - discounts["yearly"]) if "yearly" in discounts else None,
            "credits_5h": t.credits_5h,
            "credits_weekly": t.credits_weekly,
            "break_even_tasks_per_month": (t.usd_per_month / per_task) if per_task else None,
            "credits_per_task": None,
            "tasks_per_5h": None,
            "tasks_per_week": None,
            "fits_tasks_per_month": None,
            "source": t.source,
            "retrieved": t.retrieved.isoformat(),
        }
        if credits_per_usd and per_task:
            cpt = per_task * credits_per_usd  # credits per task at the 1.0 (peak, published baseline) rate
            row["credits_per_task"] = cpt
            row["tasks_per_5h"] = {
                "peak": t.credits_5h / cpt,
                "off_peak": t.credits_5h / (cpt * cp.off_peak_multiplier.value),
                "peak_reported": t.credits_5h / (cpt * cp.reported_peak_multiplier.value),
            }
            row["tasks_per_week"] = {
                "peak": t.credits_weekly / cpt,
                "off_peak": t.credits_weekly / (cpt * cp.off_peak_multiplier.value),
                "peak_reported": t.credits_weekly / (cpt * cp.reported_peak_multiplier.value),
            }
            if tasks_per_month:
                need_week = tasks_per_month / WEEKS_PER_MONTH * cpt
                row["fits_tasks_per_month"] = {
                    "weekly_credits_needed_peak": need_week,
                    "weekly_credits_needed_off_peak": need_week * cp.off_peak_multiplier.value,
                    "fits_off_peak": need_week * cp.off_peak_multiplier.value <= t.credits_weekly,
                    "fits_peak": need_week <= t.credits_weekly,
                }
        tiers.append(row)
    assumptions = [
        "One task = one trace_id; per-task figures divide the workload totals by the task count.",
        "Token totals are summed over calls that logged usage"
        + (f" ({w.calls_without_usage} call(s) without usage are left out)." if w.calls_without_usage else "."),
        "input_tokens_total includes cache reads and writes; cost = (input - cache_read) x input price + "
        "cache_read x cached-input price + output x output price. Output includes reasoning tokens.",
        "Cache writes are billed as fresh input (no write price is listed). "
        "Cached-input storage is free for a limited time.",
        "The same token counts are priced on every host; real hosts tokenise slightly differently and may cache "
        "less or more than the trace did (HEURISTIC: no per-host cache model).",
        "usd_if_cache_fixed re-prices the lost prefix tokens found by cache forensics as cached input (HEURISTIC).",
        "A host or price the table does not list reads 'not logged'. usd_upper_bound prices cached tokens at the "
        "fresh-input rate when only the cached-input price is missing.",
        f"Coding Plan: prices, the 5-hour and weekly credit caps, the peak window ({cp.peak_window.description}) "
        f"and the {cp.off_peak_multiplier.value:g}x off-peak rate are published. Credits per token are NOT published, "
        "so credits and tasks-per-window need --credits-per-usd (a HEURISTIC USD-to-credit conversion you supply).",
        f"The {cp.reported_peak_multiplier.value:g}x peak burn is an unofficial user report (HEURISTIC), "
        "shown beside the published rates.",
        f"Break-even tasks per month = tier monthly price / pay-as-you-go cost per task on {DEFAULT_HOST}/{ref_model}; "
        "it ignores the credit caps.",
    ]
    return {
        "workload": {k: getattr(w, k) for k in w.__dataclass_fields__},
        "reference": {"host": DEFAULT_HOST, "model": ref_model, "usd_per_task": per_task},
        "payg": rows,
        "coding_plan": {
            "tiers": tiers,
            "peak_window": cp.peak_window.description,
            "off_peak_multiplier": cp.off_peak_multiplier.value,
            "reported_peak_multiplier": cp.reported_peak_multiplier.value,
            "credits_per_usd": credits_per_usd,
            "tasks_per_month": tasks_per_month,
        },
        "assumptions": assumptions,
    }
