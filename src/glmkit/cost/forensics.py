"""Cache, verbosity and tokenizer forensics over SwitchTrace v0 records.

Everything here that explains *why* is HEURISTIC: a cache break is observed (usage numbers), its cause is
inferred by diffing the broken call against the call before it. Detectors are named, independent and can be
switched off (``disabled=`` or ``$GLMKIT_COST_DISABLE=name,name``) so tests can prove each attribution depends
on its detector.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from glmkit.contracts.trace import SwitchTrace, TraceMessage

DEFAULT_BREAK_THRESHOLD = 0.85  # reuse below this = a break (healthy implicit caches reuse ~100% of the prior prompt)
DEFAULT_IDLE_TTL_S = 300.0  # heuristic: gaps longer than this may have evicted the prefix (Z.ai TTL is not published)
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
DISABLE_ENV = "GLMKIT_COST_DISABLE"

# ------------------------------------------------------------------------------------------------ grouping


def group_traces(traces: Iterable[SwitchTrace]) -> dict[str, list[SwitchTrace]]:
    """trace_id -> calls in order (seq, then ts_start)."""
    out: dict[str, list[SwitchTrace]] = defaultdict(list)
    for t in traces:
        out[t.trace_id].append(t)
    for calls in out.values():
        calls.sort(key=lambda c: (c.seq, c.ts_start))
    return dict(out)


# ------------------------------------------------------------------------------------------------ detectors


@dataclass
class Cause:
    cause: str
    detail: str
    subtype: str | None = None
    confidence: str = "heuristic"

    def as_dict(self) -> dict[str, Any]:
        return {"cause": self.cause, "subtype": self.subtype, "detail": self.detail, "confidence": self.confidence}


@dataclass
class DetectorContext:
    idle_ttl_s: float = DEFAULT_IDLE_TTL_S


Detector = Callable[[SwitchTrace, SwitchTrace, DetectorContext], Cause | None]
DETECTORS: dict[str, Detector] = {}
CAUSE_LABELS = {
    "model_changed": "Model changed between calls",
    "tool_order_change": "Tool definitions reordered",
    "tool_definition_change": "Tool definitions edited, added or removed",
    "timestamp_in_prefix": "Timestamp or id in the system prompt",
    "system_prompt_churn": "System prompt changed",
    "reasoning_not_replayed": "Reasoning not replayed verbatim",
    "history_rewritten": "Earlier conversation rewritten",
    "idle_gap": "Long idle gap (cache expiry)",
}
UNATTRIBUTED = "unattributed"


def detector(name: str) -> Callable[[Detector], Detector]:
    def deco(fn: Detector) -> Detector:
        DETECTORS[name] = fn
        return fn

    return deco


_TS_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?",
    r"\d{4}-\d{2}-\d{2}",
    r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?",
    r"\b\d{10,13}\b",
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,? \d{1,2} [A-Z][a-z]{2,8} \d{4}\b",
]
_TS_RE = re.compile("|".join(f"(?:{p})" for p in _TS_PATTERNS))


def _system_text(t: SwitchTrace) -> str:
    return "\n".join(p.text or "" for p in t.request.system)


def _tools(t: SwitchTrace) -> list[tuple[str, str]]:
    return [(x.name, json.dumps(x.model_dump(), sort_keys=True)) for x in t.request.tools]


def _snippet(a: str, b: str, width: int = 70) -> str:
    """Where two strings first differ, as 'prev ⟶ cur' excerpts."""
    i = next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    lo = max(0, i - 15)
    return f"@char {i}: {a[lo : lo + width]!r} -> {b[lo : lo + width]!r}"


@detector("model_changed")
def _model_changed(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    if prev.request.model != cur.request.model:
        return Cause("model_changed", f"{prev.request.model} -> {cur.request.model}")
    return None


@detector("tool_order")
def _tool_order(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    a, b = _tools(prev), _tools(cur)
    if a != b and sorted(a) == sorted(b):
        return Cause("tool_order_change", f"same {len(a)} tools, new order: {[n for n, _ in a]} -> {[n for n, _ in b]}")
    return None


@detector("tool_definitions")
def _tool_definitions(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    a, b = _tools(prev), _tools(cur)
    if sorted(a) == sorted(b):
        return None
    na, nb = {n for n, _ in a}, {n for n, _ in b}
    if na != nb:
        return Cause("tool_definition_change", f"tools added {sorted(nb - na)} removed {sorted(na - nb)}")
    changed = sorted(n for n in na if dict(a).get(n) != dict(b).get(n))
    return Cause("tool_definition_change", f"definition of {changed} changed")


@detector("timestamp_in_prefix")
def _timestamp(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    a, b = _system_text(prev), _system_text(cur)
    if a != b and _TS_RE.sub("<ts>", a) == _TS_RE.sub("<ts>", b):
        return Cause("timestamp_in_prefix", "system prompt differs only in a time/date/id value: " + _snippet(a, b))
    return None


@detector("system_churn")
def _system_churn(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    a, b = _system_text(prev), _system_text(cur)
    if a != b and _TS_RE.sub("<ts>", a) != _TS_RE.sub("<ts>", b):
        return Cause("system_prompt_churn", "system prompt text changed: " + _snippet(a, b))
    return None


def _sig(m: TraceMessage) -> tuple[str, str, tuple[str, ...]]:
    """Order-insensitive signature: (role, reasoning text, sorted non-reasoning parts)."""
    reasoning = "".join(p.text or "" for p in m.parts if p.type == "reasoning")
    rest = sorted(json.dumps(p.model_dump(exclude_none=True), sort_keys=True) for p in m.parts if p.type != "reasoning")
    return m.role, reasoning, tuple(rest)


def _prior_conversation(prev: SwitchTrace) -> list[TraceMessage]:
    return [*prev.request.messages, *prev.response.messages]


@detector("reasoning_replay")
def _reasoning(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    for i, pm in enumerate(_prior_conversation(prev)):
        if i >= len(cur.request.messages):
            return None
        a, b = _sig(pm), _sig(cur.request.messages[i])
        if a == b:
            continue
        if a[0] == b[0] == "assistant" and a[2] == b[2] and a[1] != b[1]:
            if a[1] and not b[1]:
                return Cause(
                    "reasoning_not_replayed", f"assistant message {i}: reasoning_content dropped", subtype="dropped"
                )
            return Cause(
                "reasoning_not_replayed",
                f"assistant message {i}: reasoning altered ({len(a[1])} -> {len(b[1])} chars): " + _snippet(a[1], b[1]),
                subtype="edited",
            )
        return None  # first divergence is something else: history_rewritten decides
    return None


@detector("history_rewritten")
def _history(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    for i, pm in enumerate(_prior_conversation(prev)):
        if i >= len(cur.request.messages):
            return Cause("history_rewritten", f"history shrank: message {i} of the prior call is gone")
        a, b = _sig(pm), _sig(cur.request.messages[i])
        if a == b:
            continue
        if a[0] == b[0] == "assistant" and a[2] == b[2]:
            return None  # reasoning-only difference
        return Cause("history_rewritten", f"message {i} ({a[0]}) differs from the prior call (compaction or edit?)")
    return None


@detector("idle_gap")
def _idle(prev: SwitchTrace, cur: SwitchTrace, ctx: DetectorContext) -> Cause | None:
    gap = (cur.ts_start - prev.ts_start).total_seconds()
    if gap > ctx.idle_ttl_s:
        return Cause("idle_gap", f"{gap:.0f}s since the previous call (> {ctx.idle_ttl_s:.0f}s assumed TTL)")
    return None


def disabled_from_env() -> set[str]:
    return {x.strip() for x in os.environ.get(DISABLE_ENV, "").split(",") if x.strip()}


def attribute(
    prev: SwitchTrace, cur: SwitchTrace, *, disabled: set[str] | None = None, ctx: DetectorContext | None = None
) -> list[Cause]:
    """Every cause the enabled detectors find for the break between ``prev`` and ``cur``, in prefix order."""
    off = disabled_from_env() if disabled is None else disabled
    ctx = ctx or DetectorContext()
    out = []
    for name, fn in DETECTORS.items():
        if name in off:
            continue
        c = fn(prev, cur, ctx)
        if c:
            out.append(c)
    return out


# ------------------------------------------------------------------------------------------------ cache forensics


def _ratio(n: int | float | None, d: int | float | None) -> float | None:
    return None if n is None or not d else n / d


@dataclass
class TurnForensics:
    seq: int
    call_id: str
    ts_start: str
    model: str
    input_total: int | None
    cache_read: int | None
    cache_share: float | None  # cache_read / input_total (what the provider calls the hit rate)
    reuse: float | None  # cache_read / previous call's input_total (how much of the old prompt was reused)
    broke: bool
    lost_tokens: int | None  # prior prompt tokens that should have been cached but were not
    causes: list[Cause] = field(default_factory=list)

    @property
    def primary(self) -> str | None:
        if not self.broke:
            return None
        return self.causes[0].cause if self.causes else UNATTRIBUTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "call_id": self.call_id,
            "ts_start": self.ts_start,
            "model": self.model,
            "input_total": self.input_total,
            "cache_read": self.cache_read,
            "cache_share": self.cache_share,
            "reuse": self.reuse,
            "broke": self.broke,
            "lost_tokens": self.lost_tokens,
            "primary_cause": self.primary,
            "causes": [c.as_dict() for c in self.causes],
        }


def analyze_trace(
    calls: list[SwitchTrace],
    *,
    threshold: float = DEFAULT_BREAK_THRESHOLD,
    disabled: set[str] | None = None,
    ctx: DetectorContext | None = None,
) -> dict[str, Any]:
    turns: list[TurnForensics] = []
    for i, c in enumerate(calls):
        u = c.usage
        prev = calls[i - 1] if i else None
        expected = prev.usage.input_tokens_total if prev else None
        reuse = _ratio(u.cache_read_tokens, expected) if prev else None
        broke = bool(
            prev is not None
            and u.cache_read_tokens is not None
            and expected
            and u.cache_read_tokens < threshold * expected
        )
        lost = max(0, expected - (u.cache_read_tokens or 0)) if broke and expected else None
        causes = attribute(prev, c, disabled=disabled, ctx=ctx) if broke and prev else []
        turns.append(
            TurnForensics(
                seq=c.seq,
                call_id=c.call_id,
                ts_start=c.ts_start.isoformat(),
                model=c.request.model,
                input_total=u.input_tokens_total,
                cache_read=u.cache_read_tokens,
                cache_share=_ratio(u.cache_read_tokens, u.input_tokens_total),
                reuse=reuse,
                broke=broke,
                lost_tokens=lost,
                causes=causes,
            )
        )
    logged = [t for t in turns if t.cache_read is not None and t.input_total]
    total_in = sum(t.input_total or 0 for t in logged)
    total_cr = sum(t.cache_read or 0 for t in logged)
    breaks = [t for t in turns if t.broke]
    first = breaks[0] if breaks else None
    return {
        "trace_id": calls[0].trace_id if calls else None,
        "task_type": next((c.task_type for c in calls if c.task_type), None),
        "model": calls[0].request.model if calls else None,
        "effort": next((c.request.params.reasoning_effort for c in calls if c.request.params.reasoning_effort), None),
        "calls": len(calls),
        "input_total": total_in if logged else None,
        "cache_read": total_cr if logged else None,
        "cache_share": (total_cr / total_in) if total_in else None,
        "breaks": len(breaks),
        "first_break_seq": first.seq if first else None,
        "first_break_cause": first.primary if first else None,
        "lost_tokens": sum(t.lost_tokens or 0 for t in breaks) if logged else None,
        "turns": [t.as_dict() for t in turns],
    }


# ------------------------------------------------------------------------------------------------ verbosity


def _task_tokens(calls: list[SwitchTrace]) -> tuple[int | None, int | None]:
    outs = [c.usage.output_tokens_total for c in calls if c.usage.output_tokens_total is not None]
    rs = [c.usage.reasoning_tokens for c in calls if c.usage.reasoning_tokens is not None]
    return (sum(outs) if outs else None, sum(rs) if rs else None)


def verbosity(grouped: dict[str, list[SwitchTrace]], table: Any = None) -> dict[str, Any]:
    """Output and reasoning tokens per task (= trace) by (model, effort), plus projected savings from a lower
    effort. A measured ratio is used when the same model appears at two efforts; otherwise the table's
    heuristic factor (only max -> high is sourced); otherwise 'not logged'."""
    buckets: dict[tuple[str, str | None], list[tuple[int | None, int | None]]] = defaultdict(list)
    for calls in grouped.values():
        eff = next((c.request.params.reasoning_effort for c in calls if c.request.params.reasoning_effort), None)
        buckets[(calls[0].request.model, eff)].append(_task_tokens(calls))
    rows = []
    for (model, eff), vals in sorted(buckets.items(), key=lambda kv: (kv[0][0], _effort_rank(kv[0][1]))):
        outs = [o for o, _ in vals if o is not None]
        rs = [r for _, r in vals if r is not None]
        rows.append(
            {
                "model": model,
                "effort": eff,
                "tasks": len(vals),
                "output_per_task": statistics.fmean(outs) if outs else None,
                "median_output_per_task": statistics.median(outs) if outs else None,
                "reasoning_per_task": statistics.fmean(rs) if rs else None,
                "output_total": sum(outs) if outs else None,
                "reasoning_total": sum(rs) if rs else None,
                "reasoning_share": (sum(rs) / sum(outs)) if rs and outs and sum(outs) else None,
            }
        )
    savings = []
    for hi in rows:
        if hi["effort"] not in EFFORT_ORDER or not hi["output_per_task"]:
            continue
        for lower in EFFORT_ORDER[: _effort_rank(hi["effort"])]:
            measured = next((r for r in rows if r["model"] == hi["model"] and r["effort"] == lower), None)
            if measured and measured["output_per_task"] is not None:
                ratio = measured["output_per_task"] / hi["output_per_task"]
                savings.append(_saving(hi["model"], hi, lower, ratio, True))
                continue
            f = table.effort_factor(hi["effort"], lower) if table is not None else None
            if f:
                savings.append(_saving(hi["model"], hi, lower, f.factor, False, f.source))
    return {"rows": rows, "savings": savings}


def _effort_rank(e: str | None) -> int:
    return EFFORT_ORDER.index(e) if e in EFFORT_ORDER else -1


def _saving(
    model: str, hi: dict[str, Any], to_effort: str, factor: float, measured: bool, source: str | None = None
) -> dict[str, Any]:
    out = hi["output_per_task"]
    return {
        "model": model,
        "from_effort": hi["effort"],
        "to_effort": to_effort,
        "factor": factor,
        "basis": "measured" if measured else "heuristic",
        "source": source,
        "output_per_task_now": out,
        "output_per_task_after": out * factor,
        "tokens_saved_per_task": out * (1 - factor),
        "tasks": hi["tasks"],
    }


# ------------------------------------------------------------------------------------------------ tokenizer inflation

CONTENT_TYPES = ["json", "code", "cjk", "prose"]
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_CODE = re.compile(
    r"(^|\n)\s*(def |class |import |from \S+ import|function |const |let |#include|public |if \(|for \()|[;{}]\s*$"
)


def classify_content(text: str) -> str:
    s = text.strip()
    if s[:1] in "{[":
        try:
            json.loads(s)
            return "json"
        except ValueError:
            pass
    if s and len(_CJK.findall(s)) / len(s) > 0.2:
        return "cjk"
    if len(_CODE.findall(s)) >= 2:
        return "code"
    return "prose"


def tokenizer_inflation(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Ratio of GLM-counted to incumbent-counted input tokens by content type.

    Row: ``{"content_type"?: json|code|cjk|prose, "text"?: str, "glm_tokens": int, "incumbent_tokens": int}``.
    Ratios are sums over rows (token-weighted), so long samples count more than short ones.
    """
    acc: dict[str, list[int]] = {k: [0, 0, 0] for k in CONTENT_TYPES}
    for r in rows:
        g, i = r.get("glm_tokens"), r.get("incumbent_tokens")
        if not g or not i:
            continue
        ct = r.get("content_type") or (classify_content(r["text"]) if r.get("text") else None)
        if ct not in acc:
            continue
        acc[ct][0] += int(g)
        acc[ct][1] += int(i)
        acc[ct][2] += 1
    out = []
    for ct in CONTENT_TYPES:
        g, i, n = acc[ct]
        out.append(
            {
                "content_type": ct,
                "samples": n,
                "glm_tokens": g or None,
                "incumbent_tokens": i or None,
                "ratio": (g / i) if i else None,
            }
        )
    tg, ti = sum(a[0] for a in acc.values()), sum(a[1] for a in acc.values())
    return {"by_type": out, "overall_ratio": (tg / ti) if ti else None, "samples": sum(a[2] for a in acc.values())}


# ------------------------------------------------------------------------------------------------ composition


def input_composition(grouped: dict[str, list[SwitchTrace]]) -> list[dict[str, Any]]:
    """HEURISTIC split of input tokens by prompt component: each call's input_tokens_total is shared out by
    the character share of its system prompt, tool schemas, user turns, assistant turns, reasoning and tool
    results. Characters are only a proxy for tokens."""
    totals: dict[str, float] = defaultdict(float)
    for calls in grouped.values():
        for c in calls:
            n = c.usage.input_tokens_total
            if not n:
                continue
            chars: dict[str, int] = defaultdict(int)
            chars["system prompt"] = sum(len(p.text or "") for p in c.request.system)
            chars["tool schemas"] = sum(len(json.dumps(t.model_dump())) for t in c.request.tools)
            for m in c.request.messages:
                for p in m.parts:
                    if p.type == "reasoning":
                        chars["reasoning replayed"] += len(p.text or "")
                    elif p.type == "tool_result":
                        chars["tool results"] += len(
                            json.dumps(p.content) if not isinstance(p.content, str) else p.content
                        )
                    elif p.type == "tool_call":
                        chars["assistant tool calls"] += len(json.dumps(p.arguments)) + len(p.name or "")
                    elif m.role == "user":
                        chars["user messages"] += len(p.text or "")
                    else:
                        chars["assistant text"] += len(p.text or "")
            s = sum(chars.values())
            for k, v in chars.items():
                if s:
                    totals[k] += n * v / s
    return [{"component": k, "tokens": round(v)} for k, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v >= 1]
