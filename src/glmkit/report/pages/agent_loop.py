"""Agent loop tab: per-turn cached-token growth, a degeneracy heat strip by turn, and a stream-parity table.

Reads conformance envelopes only. Loops are items whose evidence has exchanges labelled ``turn<N>`` (THINK-001,
CACHE-002, LANG-001/002 and the THINK-006 soak); degeneracy trials are items with ``final`` exchanges
(THINK-005); the parity table lists the stream checks of STRM-001..005. The newest run per target column wins.
"""

from __future__ import annotations

import re
from typing import Any

from glmkit.agentloop.metrics import degeneracy_metrics, is_degenerate
from glmkit.contracts.envelope import ItemResult
from glmkit.contracts.evidence import Exchange
from glmkit.report.pages.conformance import column_key
from glmkit.report.registry import Page, RunRef, register_page

PARITY_CHECKS = {"stream_nonstream_parity", "tool_stream_fragments", "stream_split_independent", "usage_parity"}
TURN = re.compile(r"^turn(\d+)$")


def _score(e: Exchange) -> float:
    n = e.normalized
    return max(degeneracy_metrics(n.reasoning)["score"], degeneracy_metrics(n.content)["score"])


def _degenerate(e: Exchange) -> bool:
    n = e.normalized
    return any(is_degenerate(degeneracy_metrics(t)) for t in (n.reasoning, n.content) if t)


def _usage(e: Exchange | None) -> dict[str, int | None]:
    u = e.normalized.usage if e else None
    return {
        "prompt": u.prompt_tokens if u else None,
        "cached": u.cached_tokens if u else None,
        "completion": u.completion_tokens if u else None,
    }


def _checks(item: ItemResult) -> list[dict[str, Any]]:
    return [{"name": c.name, "status": c.status, "message": c.message, "soft": c.soft} for c in item.checks]


def loop_record(item: ItemResult, col: str, run_id: str) -> dict[str, Any] | None:
    turns = [e for e in item.evidence if TURN.match(e.label)]
    if not turns:
        return None
    turns.sort(key=lambda e: int(TURN.match(e.label).group(1)))  # type: ignore[union-attr]
    rows = []
    for e in turns:
        u = _usage(e)
        n = e.normalized
        rows.append(
            {
                "label": e.label,
                "http": n.http_status,
                "prompt": u["prompt"],
                "cached": u["cached"],
                "completion": u["completion"],
                "score": _score(e) if n.ok else None,
                "degenerate": _degenerate(e) if n.ok else None,
                "tool_calls": len(n.tool_calls),
                "finish": n.finish_reason_norm,
            }
        )
    last = next((r for r in reversed(rows) if r["prompt"]), None)
    share = (last["cached"] or 0) / last["prompt"] if last else None
    return {
        "key": f"{col}::{item.id}",
        "item": item.id,
        "group": item.group,
        "title": item.title,
        "column": col,
        "run_id": run_id,
        "status": item.status,
        "kind": item.kind,
        "turns": rows,
        "cached_share": share,
        "degenerate_turns": sum(1 for r in rows if r["degenerate"]),
        "checks": _checks(item),
        "reason": item.reason,
    }


def trial_record(item: ItemResult, col: str, run_id: str) -> dict[str, Any] | None:
    finals = [e for e in item.evidence if e.label == "final"]
    if not finals:
        return None
    return {
        "key": f"{col}::{item.id}",
        "item": item.id,
        "title": item.title,
        "column": col,
        "run_id": run_id,
        "status": item.status,
        "trials": [
            {"label": f"trial {i}", "score": _score(e) if e.normalized.ok else None, "degenerate": _degenerate(e)}
            for i, e in enumerate(finals)
        ],
        "attempts": item.metrics.get("attempts"),
        "pass_rate": item.metrics.get("pass_rate"),
        "checks": _checks(item),
    }


def parity_rows(item: ItemResult, col: str, run_id: str) -> list[dict[str, Any]]:
    by = {e.label: e for e in item.evidence}
    ns, st = by.get("nonstream"), by.get("stream")
    splits = [e for e in item.evidence if e.label.startswith("split-")]
    out = []
    for c in item.checks:
        if c.name not in PARITY_CHECKS:
            continue
        out.append(
            {
                "key": f"{col}::{item.id}::{c.name}",
                "item": item.id,
                "title": item.title,
                "column": col,
                "check": c.name,
                "status": c.status,
                "message": c.message,
                "tools": [t.name for t in (ns.normalized.tool_calls if ns else [])],
                "finish": [
                    ns.normalized.finish_reason_norm if ns else None,
                    st.normalized.finish_reason_norm if st else None,
                ],
                "usage": {"nonstream": _usage(ns), "stream": _usage(st)} if ns and st else None,
                "splits": len(splits) or None,
                "checks": _checks(item),
            }
        )
    return out


@register_page
class AgentLoopPage(Page):
    id = "agent_loop"
    title = "Agent loop"
    order = 40
    script = "agent_loop.js"
    css = "agent_loop.css"

    def collect(self, runs: list[RunRef]) -> dict[str, Any] | None:
        loops: list[dict[str, Any]] = []
        trials: list[dict[str, Any]] = []
        parity: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in runs:  # newest first
            env = ref.envelope
            if env.component != "conform":
                continue
            col = column_key(ref)
            for item in env.items:
                key = f"{col}::{item.id}"
                if key in seen:
                    continue
                recs = (loop_record(item, col, env.run_id), trial_record(item, col, env.run_id))
                pr = parity_rows(item, col, env.run_id)
                if not (recs[0] or recs[1] or pr):
                    continue
                seen.add(key)
                if recs[0]:
                    loops.append(recs[0])
                if recs[1]:
                    trials.append(recs[1])
                parity += pr
        if not (loops or trials or parity):
            return None
        turns = sum(len(lp["turns"]) for lp in loops)
        degenerate = sum(lp["degenerate_turns"] for lp in loops) + sum(
            1 for t in trials for x in t["trials"] if x["degenerate"]
        )
        shares = [
            lp["cached_share"] for lp in loops if lp["cached_share"] is not None and lp["group"].startswith("CACHE")
        ]
        bad_parity = sum(1 for p in parity if p["status"] in ("fail", "error"))
        return {
            "count": len(loops) + len(trials),
            "loops": loops,
            "trials": trials,
            "parity": parity,
            "kpis": [
                {"label": "Loops", "value": len(loops), "sub": f"{turns} turns recorded"},
                {
                    "label": "Cached share",
                    "value": (sum(shares) / len(shares)) if shares else None,
                    "format": "pct",
                    "sub": "last turn of the cache loops",
                },
                {
                    "label": "Degenerate turns",
                    "value": degenerate,
                    "sub": "loops and replay trials",
                    "tone": "err" if degenerate else None,
                },
                {
                    "label": "Parity failures",
                    "value": bad_parity,
                    "sub": f"of {len(parity)} stream checks",
                    "tone": "err" if bad_parity else None,
                },
            ],
        }
