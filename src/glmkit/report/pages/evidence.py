"""Evidence tab: checklist status by framework (stacked bars), gaps first, links to the evidence runs.

Data comes from result envelopes with component ``evidence`` (written by ``glmkit evidence build``); the newest
envelope per deployment is shown. Every view carries the not-legal-advice disclaimer.
"""

from __future__ import annotations

from typing import Any

from glmkit.evidence.disclaimer import DISCLAIMER
from glmkit.report.registry import Page, RunRef, register_page

STATUS_ORDER = ("gap", "needs-counsel", "met", "n/a")


def _deployment(ref: RunRef) -> dict[str, Any]:
    env = ref.envelope
    d = env.data
    items = []
    for it in env.items:
        x = it.data
        items.append(
            {
                "id": it.id,
                "framework": it.group,
                "title": it.title,
                "status": x.get("checklist_status", "gap"),
                "reason": it.reason,
                "severity": it.severity,
                "prompts": x.get("prompts", []),
                "citations": x.get("citations", []),
                "in_force": x.get("in_force"),
                "flags": x.get("flags", []),
                "interpretive": x.get("interpretive", False),
                "attested": x.get("attested", False),
                "evidence": x.get("evidence", []),
            }
        )
    items.sort(key=lambda i: (STATUS_ORDER.index(i["status"]) if i["status"] in STATUS_ORDER else 9, i["id"]))
    dep = d.get("deployment", {})
    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUS_ORDER}
    return {
        "run_id": env.run_id,
        "path": ref.path,
        "started_at": env.started_at.isoformat(),
        "verdict": env.verdict,
        "name": dep.get("name", env.config.get("deployment", env.run_id)),
        "path_title": d.get("path_title"),
        "hosting": dep.get("hosting", {}),
        "license": d.get("license"),
        "bom_valid": d.get("bom_valid"),
        "pack_dir": d.get("pack_dir"),
        "frameworks": d.get("frameworks", []),
        "evidence_index": d.get("evidence_index", []),
        "items": items,
        "counts": counts,
        "kpis": [
            {
                "label": "Checklist items",
                "value": len(items),
                "sub": f"{len(d.get('evidence_index', []))} linked run(s)",
            },
            {"label": "Met", "value": counts["met"], "sub": "with an evidence link", "tone": None},
            {
                "label": "Gaps",
                "value": counts["gap"],
                "sub": "no evidence, or settled against",
                "tone": "err" if counts["gap"] else None,
            },
            {
                "label": "Needs counsel",
                "value": counts["needs-counsel"],
                "sub": "depends on legal interpretation",
                "tone": "warn" if counts["needs-counsel"] else None,
            },
            {"label": "Not applicable", "value": counts["n/a"], "sub": "for this path or jurisdictions"},
        ],
    }


@register_page
class EvidencePage(Page):
    id = "evidence"
    title = "Evidence"
    order = 100  # after the other tabs
    script = "evidence.js"
    css = "evidence.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any]:
        seen: set[str] = set()
        deployments = []
        for ref in runs:  # newest first
            if ref.envelope.component != "evidence":
                continue
            d = _deployment(ref)
            if d["name"] in seen:
                continue
            seen.add(d["name"])
            deployments.append(d)
        return {
            "count": len(deployments),
            "disclaimer": DISCLAIMER,
            "deployments": deployments,
            "kpis": deployments[0]["kpis"] if deployments else [],
        }
