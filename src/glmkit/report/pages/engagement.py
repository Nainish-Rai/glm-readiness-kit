"""Engagement tab: the brief, the checklist of steps (derived from the workspace's files and results), the mode the
last demo run used, and links to each component's tab.

The tab appears only when the dashboard is built inside an engagement workspace (``engagement.yaml`` in the working
directory or a parent, or ``$GLMKIT_WORKSPACE``). Step state is derived from files and result envelopes, never from a
flag someone edits.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from glmkit.report.registry import Page, RunRef, register_page
from glmkit.workbench.workspace import BRIEF_SECTIONS, MARKER, derive_state, find_workspace

PLAN_DAYS = 90


def workspace_root() -> Path | None:
    env = os.environ.get("GLMKIT_WORKSPACE")
    if env and (Path(env) / MARKER).is_file():
        return Path(env)
    return find_workspace(Path.cwd())


@register_page
class EngagementPage(Page):
    id = "engagement"
    title = "Engagement"
    order = 5
    script = "engagement.js"
    css = "engagement.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        root = workspace_root()
        if root is None:
            return None
        # The dashboard is being built right now, so the report step is done by definition.
        state = derive_state(root, [r.envelope for r in runs], site_built=True)
        try:
            created = date.fromisoformat(state.created)
            day = (datetime.now(UTC).date() - created).days + 1
        except ValueError:
            day = None
        last: dict[str, Any] | None = None
        lr = root / "demo" / "last-run.json"
        if lr.is_file():
            try:
                last = json.loads(lr.read_text(encoding="utf-8"))
            except ValueError:
                last = None
        sets = (last or {}).get("sets") or []
        modes = sorted({s["mode"] for s in sets})
        steps = [s.__dict__ for s in state.steps]
        brief = [
            {"title": k, "text": state.brief.get(k, "")}
            for k in [*BRIEF_SECTIONS, *[k for k in state.brief if k not in BRIEF_SECTIONS]]
            if k in state.brief
        ]
        return {
            "count": state.done,
            "customer": state.customer,
            "created": state.created,
            "day": day,
            "plan_days": PLAN_DAYS,
            "steps": steps,
            "brief": brief,
            "brief_todos": state.brief_todos,
            "last_run": {"sets": sets, "targets": (last or {}).get("targets"), "modes": modes} if last else None,
            "kpis": [
                {
                    "label": "Steps done",
                    "value": state.done,
                    "unit": f"/ {len(steps)}",
                    "sub": "derived from files and results",
                },
                {"label": "Engagement day", "value": day, "unit": f"/ {PLAN_DAYS}", "sub": f"started {state.created}"},
                {
                    "label": "Brief TODOs",
                    "value": state.brief_todos,
                    "sub": "lines left in brief.md",
                    "tone": "warn" if state.brief_todos else None,
                },
                {
                    "label": "Sets fetched",
                    "value": sum(1 for x in sets if x["mode"] == "fetched"),
                    "unit": f"/ {len(sets)}",
                    "sub": "stand-in items fill the rest",
                },
            ],
        }
