"""Bridge tab: fix events by type over time (stacked bars), top fixes (bar list) and an event table.

Reads every ``component: bridge`` envelope (one per ``glmkit bridge serve`` session); each ``fix_event`` item is
one applied fix. Events carry no message content unless the session ran with ``--log-content``.

Protocol filter: an event's protocol is ``details.protocol`` (``anthropic`` for the ``/v1/messages`` path of issue 07),
otherwise ``openai``. The payload carries one ``views`` entry per protocol (and ``all``) so the chart, the top list,
the KPI strip and the table all follow the filter.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from glmkit.bridge.core.events import FIX_TITLES
from glmkit.report.registry import Page, RunRef, register_page

BUCKET_SECONDS = (1, 5, 10, 30, 60, 300, 900, 3600, 6 * 3600, 86400)
MAX_BUCKETS = 24
PROTOCOLS = (("openai", "OpenAI chat"), ("anthropic", "Anthropic Messages"))
MAX_SERIES = 7  # --cat-1..7, the rest fold into "other" (--cat-8)


def _ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def bucketize(events: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Stacked-bar input: (labels, series) with one series per fix id (top ones), counts per time bucket."""
    if not events:
        return [], []
    times = [_ts(e["ts"]) for e in events]
    lo, hi = min(times), max(times)
    width = next((w for w in BUCKET_SECONDS if (hi - lo) / w < MAX_BUCKETS), BUCKET_SECONDS[-1])
    start = lo - (lo % width)
    n = int((hi - start) // width) + 1
    fmt = "%H:%M:%S" if width < 60 else "%H:%M" if width < 86400 else "%m-%d"
    labels = [datetime.fromtimestamp(start + i * width).astimezone().strftime(fmt) for i in range(n)]
    totals = Counter(e["fix_id"] for e in events)
    top = [k for k, _ in totals.most_common(MAX_SERIES)]
    names = top + (["other"] if len(totals) > len(top) else [])
    values = {k: [0] * n for k in names}
    for e, t in zip(events, times, strict=True):
        key = e["fix_id"] if e["fix_id"] in top else "other"
        values[key][int((t - start) // width)] += 1
    series = [
        {"name": k, "color": f"--cat-{i + 1}" if k != "other" else "--cat-8", "values": values[k]}
        for i, k in enumerate(names)
    ]
    return labels, series


def protocol_of_event(d: dict[str, Any]) -> str:
    return str((d.get("details") or {}).get("protocol") or "openai")


def protocol_of_path(path: str | None) -> str:
    p = (path or "").rstrip("/")
    return "anthropic" if p.endswith(("/messages", "/messages/count_tokens")) else "openai"


def make_view(events: list[dict[str, Any]], reqs: list[dict[str, Any]], sessions: int) -> dict[str, Any]:
    labels, series = bucketize(events)
    by_fix = Counter(e["fix_id"] for e in events)
    failed = Counter(e["fix_id"] for e in events if not e["ok"])
    top = [
        {"fix_id": k, "title": FIX_TITLES.get(k, k), "count": v, "failed": failed.get(k, 0)}
        for k, v in by_fix.most_common()
    ]
    fixed = sum(1 for r in reqs if r.get("fixes"))
    unrepaired = by_fix.get("tool-args-unrepaired", 0)
    return {
        "count": len(events),
        "labels": labels,
        "series": series,
        "top": top,
        "kpis": [
            {"label": "Fix events", "value": len(events), "sub": f"{len(by_fix)} fix type(s), {sessions} session(s)"},
            {"label": "Requests", "value": len(reqs), "sub": "through the bridge"},
            {
                "label": "Requests fixed",
                "value": (fixed / len(reqs)) if reqs else None,
                "format": "pct",
                "sub": f"{fixed} of {len(reqs)} needed at least one fix",
            },
            {
                "label": "Retries",
                "value": by_fix.get("tool-args-retry", 0),
                "sub": "parse error injected, at most 1 per call",
            },
            {
                "label": "Unrepaired",
                "value": unrepaired,
                "sub": "error returned, nothing substituted",
                "tone": "err" if unrepaired else None,
            },
        ],
    }


@register_page
class BridgePage(Page):
    id = "bridge"
    title = "Bridge"
    order = 40
    script = "bridge.js"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        sessions = [r for r in runs if r.envelope.component == "bridge"]
        if not sessions:
            return None
        events: list[dict[str, Any]] = []
        items: dict[str, Any] = {}
        requests: list[dict[str, Any]] = []
        for ref in sessions:
            env = ref.envelope
            for r in env.data.get("requests") or []:
                requests.append({**r, "protocol": protocol_of_path(r.get("path"))})
            run = {
                "run_id": env.run_id,
                "started_at": env.started_at.isoformat(),
                "upstream": (env.targets[0].profile if env.targets else None),
                "flavor": env.config.get("flavor"),
                "source": env.config.get("source") or "proxy",
                "log_content": bool(env.config.get("log_content")),
            }
            for it in env.items:
                if it.kind != "fix_event":
                    continue
                d = it.data
                key = f"{env.run_id}::{it.id}"
                events.append(
                    {
                        "id": key,
                        "ts": d.get("ts") or env.started_at.isoformat(),
                        "fix_id": d.get("fix_id") or it.group or "unknown",
                        "title": it.title,
                        "request_id": d.get("request_id"),
                        "upstream": d.get("upstream"),
                        "protocol": protocol_of_event(d),
                        "source": d.get("source") or env.config.get("source") or "proxy",
                        "before": d.get("before"),
                        "after": d.get("after"),
                        "ok": bool(d.get("ok", True)),
                        "run_id": env.run_id,
                    }
                )
                items[key] = {"event": d, "title": it.title, "run": run}
        events.sort(key=lambda e: e["ts"], reverse=True)
        seen = {e["protocol"] for e in events} | {r["protocol"] for r in requests}
        protocols = [{"id": "all", "label": "All"}] + [{"id": i, "label": t} for i, t in PROTOCOLS if i in seen]
        views = {"all": make_view(events, requests, len(sessions))}
        for pr in protocols[1:]:
            pid = pr["id"]
            views[pid] = make_view(
                [e for e in events if e["protocol"] == pid],
                [r for r in requests if r["protocol"] == pid],
                len(sessions),
            )
        return {
            **views["all"],
            "events": events,
            "items": items,
            "sessions": len(sessions),
            "protocols": protocols,
            "views": views,
        }
