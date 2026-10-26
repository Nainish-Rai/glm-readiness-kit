"""Errors tab: conformance failures grouped by Z.ai error-taxonomy category, plus the token-accounting ratio
chart per corpus (ANTH-004) and the taxonomy table itself (glmkit.zai_errors)."""

from __future__ import annotations

from typing import Any

from glmkit.contracts.envelope import ItemResult
from glmkit.report.pages.conformance import column_key
from glmkit.report.registry import Page, RunRef, register_page
from glmkit.zai_errors import CATEGORIES, classify, table_rows

FAILING = ("fail", "soft_fail", "error", "throttled")
# categories that are not in the taxonomy: no upstream error to classify
EXTRA_CATEGORIES = {
    "transport": ("No response", "Timeout, stalled stream or connection failure: no HTTP answer to classify."),
    "contract": ("Contract violation", "The host answered without an error code, but the answer broke the contract."),
}
COLORS = {
    "auth": "--cat-1",
    "billing": "--cat-2",
    "permission": "--cat-3",
    "usage_limit": "--cat-4",
    "rate_limit": "--warn",
    "overloaded": "--cat-5",
    "invalid_request": "--cat-6",
    "not_found": "--cat-7",
    "context_length": "--cat-8",
    "content_safety": "--cat-1",
    "server": "--err",
    "unknown": "--warn",
    "transport": "--err",
    "contract": "--warn",
}


def observed_error(item: ItemResult) -> dict[str, Any]:
    """The upstream error behind a failing item: first evidence exchange with a 4xx/5xx or a transport failure."""
    for ex in item.evidence:
        n = ex.normalized
        if ex.transport_error:
            return {"category": "transport", "code": None, "http": n.http_status, "detail": ex.transport_error}
        if n.http_status is not None and n.http_status >= 400:
            info = classify(
                n.error_code,
                http_status=n.http_status,
                message=n.error_message,
                headers=ex.response.headers if ex.response else None,
            )
            return {
                "category": info.category,
                "code": n.error_code,
                "http": n.http_status,
                "detail": (n.error_message or (ex.response.body_text if ex.response else "") or "")[:140],
                "known": info.known,
            }
    return {"category": "contract", "code": None, "http": None, "detail": ""}


def ratio_rows(item: ItemResult) -> list[dict[str, Any]] | None:
    for c in item.checks:
        if c.name == "wire_token_ratio" and isinstance(c.observed, list):
            return [r for r in c.observed if isinstance(r, dict)]
    return None


@register_page
class ErrorsPage(Page):
    id = "errors"
    title = "Errors"
    order = 25
    script = "errors.js"
    css = "errors.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        conform = [r for r in runs if r.envelope.component == "conform"]  # newest first
        if not conform:
            return None
        seen_cols: set[str] = set()
        buckets: dict[str, dict[str, dict[str, Any]]] = {}  # category -> type -> record
        items: dict[str, Any] = {}
        ratios: list[dict[str, Any]] = []
        scored = failing = leaks = 0
        for ref in conform:
            env = ref.envelope
            col = column_key(ref)
            if col in seen_cols:
                continue  # an older run of the same column
            seen_cols.add(col)
            run_meta = {
                "run_id": env.run_id,
                "started_at": env.started_at.isoformat(),
                "target": col,
                "defects": env.config.get("mock_defects") or [],
            }
            for it in env.items:
                if it.status != "skip":
                    scored += 1
                if it.group == "ANTH-004" and it.status != "skip":
                    rows = ratio_rows(it)
                    if rows:
                        ratios.append(
                            {
                                "column": col,
                                "run_id": env.run_id,
                                "item": it.id,
                                "status": it.status,
                                "limit": 1.3,
                                "rows": rows,
                            }
                        )
                if it.status not in FAILING:
                    continue
                failing += 1
                err = observed_error(it)
                if err["http"] and err["http"] >= 500:
                    leaks += 1
                key = f"{err['code'] or 'no code'} · HTTP {err['http'] or 'none'}"
                rec = buckets.setdefault(err["category"], {}).setdefault(
                    key, {"type": key, "count": 0, "heads": it.reason or err["detail"], "sessions": []}
                )
                rec["count"] += 1
                ref_id = f"{env.run_id}::{it.id}"
                rec["sessions"].append({"id": ref_id, "label": it.id, "tip": f"{it.id} on {col}\n{it.reason or ''}"})
                items[ref_id] = {"item": it.model_dump(mode="json"), "run": run_meta}
        titles = {**{k: v[0] for k, v in CATEGORIES.items()}, **{k: v[0] for k, v in EXTRA_CATEGORIES.items()}}
        order = [*CATEGORIES, *EXTRA_CATEGORIES]
        groups = []
        for cat in sorted(buckets, key=lambda c: order.index(c)):
            kinds = sorted(buckets[cat].values(), key=lambda r: -r["count"])
            count = sum(k["count"] for k in kinds)
            groups.append(
                {
                    "id": cat,
                    "label": titles[cat],
                    "color": COLORS.get(cat),
                    "count": count,
                    "rate": f"{count} of {scored} items",
                    "items": kinds,
                }
            )
        return {
            "count": failing,
            "kpis": [
                {
                    "label": "Failing items",
                    "value": failing,
                    "sub": f"of {scored} items in the latest runs",
                    "tone": "err" if failing else None,
                },
                {"label": "Categories", "value": len(groups), "sub": "taxonomy categories hit"},
                {
                    "label": "Server errors",
                    "value": leaks,
                    "sub": "failing items that saw HTTP 5xx",
                    "tone": "err" if leaks else None,
                },
                {
                    "label": "Worst token ratio",
                    "value": max((r["ratio"] or 0 for x in ratios for r in x["rows"]), default=None),
                    "format": "pct",
                    "sub": "reported vs reference tokens (limit 130%)",
                },
            ],
            "groups": groups,
            "items": items,
            "ratios": ratios,
            "taxonomy": table_rows(),
            "categories": {k: {"title": v[0], "meaning": v[1]} for k, v in {**CATEGORIES, **EXTRA_CATEGORIES}.items()},
        }
