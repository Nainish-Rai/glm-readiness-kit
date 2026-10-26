"""Deploy tab: interactive MoE sizing calculator (runs offline in the page) plus saved ``deploy size`` runs."""

from __future__ import annotations

from typing import Any

from glmkit.deploy.sizing import Inputs, build_payload, fit_calibration, load_catalog, size, validate
from glmkit.report.registry import Page, RunRef, register_page


@register_page
class DeployPage(Page):
    id = "deploy"
    title = "Deploy"
    order = 60
    script = "deploy.js"
    css = "deploy.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        catalog = load_catalog()
        payload = build_payload(catalog)
        defaults = size(Inputs(), payload)
        saved = []
        for r in runs:
            if r.envelope.component != "deploy":
                continue
            sz = r.envelope.data.get("sizing") or {}
            cost, feas = sz.get("cost") or {}, sz.get("feasibility") or {}
            saved.append(
                {
                    "id": r.envelope.run_id,
                    "path": r.path,
                    "started_at": r.envelope.started_at.isoformat(),
                    "verdict": r.envelope.verdict,
                    "inputs": sz.get("inputs"),
                    "feasible": feas.get("feasible"),
                    "reasons": feas.get("reasons") or [],
                    "self_usd_m": cost.get("self_usd_m"),
                    "api_usd_m": cost.get("api_usd_m"),
                    "breakeven_utilisation": cost.get("breakeven_utilisation"),
                }
            )
        val = validate()
        return {
            "payload": payload,
            "defaults": defaults["inputs"],
            "calibration": catalog["calibration"],
            "checkpoint_sources": catalog["checkpoints"],
            "fit": fit_calibration(payload, catalog["calibration"]),
            "validation": val,
            "saved": saved,
            "kpis": [
                {
                    "label": "FP8 weights",
                    "value": defaults["weights"]["total_gb"],
                    "unit": "GB",
                    "sub": "checkpoint file sizes",
                },
                {
                    "label": "KV per token",
                    "value": defaults["kv"]["bytes_per_token_mla"] / 1000,
                    "unit": "KB",
                    "sub": "78 x (512 + 64) x 1 B, MLA latent",
                },
                {
                    "label": "KV tokens, 8xH200",
                    "value": defaults["kv"]["max_tokens"],
                    "format": "k",
                    "sub": "per node, replicated across TP ranks",
                },
                {"label": "Saved sizing runs", "value": len(saved), "sub": "from glmkit deploy size"},
            ],
        }
