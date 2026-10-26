"""Hosts tab: host-quality runs (``glmkit conform host``), one column of results per host.

Latency (TTFT with and without tools, tokens/s by effort), fingerprint gates with their p-values, and the
capability canaries. This is the host-quality verdict, kept apart from the correctness verdict on the
Conformance tab.
"""

from __future__ import annotations

from typing import Any

from glmkit.report.registry import Page, RunRef, register_page

MAX_STR = 1500  # long strings in drawer evidence (the 128k needle prompt) are cut for the site data
MAX_EXCHANGES = 4


def _clip(v: Any) -> Any:
    if isinstance(v, str):
        return v if len(v) <= MAX_STR else v[:MAX_STR] + f"... [{len(v) - MAX_STR} more characters not shown]"
    if isinstance(v, list):
        return [_clip(x) for x in v]
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    return v


def _slim_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    ev = item.get("evidence") or []
    out["evidence"] = [_clip(e) for e in ev[:MAX_EXCHANGES]]
    out["checks"] = [{**c, "observed": _clip(c.get("observed"))} for c in item.get("checks", [])]
    if len(ev) > MAX_EXCHANGES:
        out["reason"] = f"{item.get('reason') or ''} (evidence: first {MAX_EXCHANGES} of {len(ev)} requests)".strip()
    return out


def _pct_of_ref(canary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not canary:
        return None
    r = canary.get("percent_of_reference") or {}
    return {
        "correct": canary.get("correct"),
        "n": canary.get("n"),
        "accuracy": canary.get("accuracy"),
        "ci95": canary.get("ci95"),
        "by_family": canary.get("by_family"),
        "ratio": r.get("ratio"),
        "lo": r.get("lo"),
        "hi": r.get("hi"),
        "ref": canary.get("ref"),
    }


@register_page
class HostsPage(Page):
    id = "hosts"
    title = "Hosts"
    order = 60
    script = "hosts.js"
    css = "hosts.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        mine = [r for r in runs if r.envelope.component == "hostq"]
        if not mine:
            return None
        hosts: list[dict[str, Any]] = []
        items: dict[str, Any] = {}
        seen: set[str] = set()
        for ref in mine:  # newest first: the latest run of each host label wins
            env = ref.envelope
            hq = (env.data or {}).get("host_quality") or {}
            key = hq.get("label") or env.config.get("label") or (env.targets[0].profile if env.targets else "unknown")
            if key in seen:
                continue
            seen.add(key)
            sections = hq.get("sections") or {}
            t = env.targets[0] if env.targets else None
            run_meta = {
                "run_id": env.run_id,
                "started_at": env.started_at.isoformat(),
                "target": key,
                "defects": env.config.get("mock_defects") or [],
            }
            gate_items = {}
            for g in hq.get("gates", []):
                it = next((i for i in env.items if i.id == g["item"]), None)
                if it is not None:
                    ref_id = f"{env.run_id}::{it.id}"
                    items[ref_id] = {"item": _slim_item(it.model_dump(mode="json")), "run": run_meta}
                    gate_items[g["id"]] = ref_id
            lat1 = sections.get("LAT-001") or {}
            lat2 = sections.get("LAT-002") or {}
            tests = lat1.get("tests") or {}
            hosts.append(
                {
                    "key": key,
                    "profile": t.profile if t else None,
                    "model": t.model if t else None,
                    "endpoint_class": t.endpoint_class if t else None,
                    "defects": env.config.get("mock_defects") or [],
                    "run_id": env.run_id,
                    "started_at": env.started_at.isoformat(),
                    "verdict": hq.get("verdict") or env.verdict,
                    "reason": hq.get("reason"),
                    "reference": hq.get("reference"),
                    "gates": [{**g, "ref": gate_items.get(g["id"])} for g in hq.get("gates", [])],
                    "tools": {
                        "conditions": lat1.get("conditions") or {},
                        "tests": tests,
                        "max_delta_s": lat1.get("max_delta_s"),
                    },
                    "efforts": lat2.get("efforts") or {},
                    "identity": sections.get("FP-001"),
                    "action": sections.get("FP-002"),
                    "text": sections.get("FP-003"),
                    "greedy": sections.get("FP-004"),
                    "canary": _pct_of_ref(sections.get("FP-005")),
                }
            )
        flagged = [h for h in hosts if h["verdict"] in ("fail", "warn")]
        ratios = [h["canary"]["ratio"] for h in hosts if h["canary"] and h["canary"]["ratio"] is not None]
        penalties = [t["delta"] for h in hosts for t in h["tools"]["tests"].values() if t.get("delta") is not None]
        return {
            "count": len(hosts),
            "hosts": hosts,
            "items": items,
            "kpis": [
                {"label": "Hosts", "value": len(hosts), "sub": "latest run per host"},
                {
                    "label": "Flagged",
                    "value": len(flagged),
                    "sub": "host-quality verdict warn or fail",
                    "tone": "err" if any(h["verdict"] == "fail" for h in hosts) else ("warn" if flagged else None),
                },
                {
                    "label": "Lowest canary score",
                    "value": min(ratios) if ratios else None,
                    "format": "pct",
                    "sub": "percent of reference",
                    "tone": "warn" if ratios and min(ratios) < 0.9 else None,
                },
                {
                    "label": "Worst tool penalty",
                    "value": max(penalties) if penalties else None,
                    "format": "s",
                    "sub": "median TTFT with tools minus without",
                    "tone": "warn" if penalties and max(penalties) > 1.0 else None,
                },
            ],
        }
