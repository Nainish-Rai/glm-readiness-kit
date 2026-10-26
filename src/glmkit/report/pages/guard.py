# ruff: noqa: E501
"""Guard tab: budget burn-down per session, loop interventions, blocked commands, audit-chain integrity.

Reads every ``component: guard`` envelope (one per guard-enabled ``glmkit bridge serve`` session). The audit chain
is re-verified at build time when the log file is reachable; otherwise the status recorded in the envelope shows,
marked as a snapshot. No message content is ever in these envelopes: tool names, rule ids, counts and hashes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from glmkit.guard.audit import verify
from glmkit.report.registry import Page, RunRef, register_page


def audit_status(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Live verification when the log is on this machine, else the envelope's snapshot."""
    snap = snapshot or {}
    path = snap.get("path")
    if path and Path(path).exists():
        res = verify(Path(path)).to_dict()
        return {**res, "live": True}
    if snap.get("ok") is None and not snap.get("entries"):
        return {"ok": None, "entries": 0, "error": snap.get("error"), "path": path, "live": False}
    return {**snap, "live": False}


@register_page
class GuardPage(Page):
    id = "guard"
    title = "Guard"
    order = 50
    script = "guard.js"
    css = "guard.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        guards = [r for r in runs if r.envelope.component == "guard"]
        if not guards:
            return None
        sessions: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for ref in guards:
            env = ref.envelope
            for s in env.data.get("sessions") or []:
                sessions.append({**s, "run_id": env.run_id, "started_at": env.started_at.isoformat()})
            for it in env.items:
                if it.kind == "guard_event":
                    events.append({**it.data, "run_id": env.run_id, "id": f"{env.run_id}::{it.id}", "title": it.title})
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        audit = audit_status(guards[0].envelope.data.get("audit"))
        loops = [e for e in events if e["kind"] == "loop"]
        blocked = [e for e in events if e["kind"] == "command"]
        enforced = [e for e in blocked if e.get("enforced", "block") == "block"]
        usd = sum(s.get("usd") or 0 for s in sessions)
        unpriced = sum(s.get("unpriced_calls") or 0 for s in sessions)
        broken = audit.get("ok") is False
        return {
            "count": len(events),
            "sessions": sessions,
            "events": events,
            "audit": audit,
            "kpis": [
                {
                    "label": "Sessions",
                    "value": len(sessions),
                    "sub": f"{sum(s.get('requests') or 0 for s in sessions)} requests",
                },
                {
                    "label": "Spend",
                    "value": usd,
                    "format": "usd",
                    "sub": "lower bound: some calls not priced" if unpriced else "from the versioned price table",
                },
                {
                    "label": "Loop interventions",
                    "value": len(loops),
                    "sub": f"{sum(1 for e in loops if e['action'] == 'block')} stopped, {sum(1 for e in loops if e['action'] == 'inject')} rethink message",
                    "tone": "warn" if loops else None,
                },
                {
                    "label": "Blocked commands",
                    "value": len(enforced),
                    "sub": f"{len(blocked) - len(enforced)} asks let through"
                    if len(blocked) > len(enforced)
                    else "removed before the client saw them",
                    "tone": "warn" if enforced else None,
                },
                {
                    "label": "Audit chain",
                    "value": audit.get("entries") or 0,
                    "unit": "BROKEN" if broken else ("intact" if audit.get("ok") else ""),
                    "sub": (audit.get("error") or "entries verified")
                    if broken or audit.get("ok") is None
                    else ("verified now" if audit.get("live") else "snapshot from the run"),
                    "tone": "err" if broken else None,
                },
            ],
        }
