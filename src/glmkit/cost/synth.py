"""Synthetic agent traces with *planted* cache breaks, for tests, the demo and the dashboard.

Usage numbers come from a tiny prefix-cache simulator, not from the detectors: ``cache_read`` is the longest
common prefix (in chars / 4) between this call's serialised request and the previous call's, zero when the
idle gap exceeds the simulated TTL, so a planted change breaks the cache the way a real implicit cache would.
Nothing here calls a model, and nothing is a measurement of GLM: it is labelled ``synthetic`` everywhere.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from glmkit.contracts.trace import (
    Part,
    SwitchTrace,
    TraceMessage,
    TraceParams,
    TraceRequest,
    TraceResponse,
    TraceSource,
    TraceTool,
    TraceUsage,
)

# scenario -> the cause the forensics must name (None = healthy, no break; "unattributed" = nothing to find)
SCENARIOS: dict[str, str | None] = {
    "healthy": None,
    "timestamp-in-prefix": "timestamp_in_prefix",
    "system-prompt-churn": "system_prompt_churn",
    "tool-reorder": "tool_order_change",
    "reasoning-edited": "reasoning_not_replayed",
    "reasoning-dropped": "reasoning_not_replayed",
    "history-compaction": "history_rewritten",
    "idle-gap": "idle_gap",
    "host-eviction": "unattributed",
}
BREAK_AT = 3  # seq of the planted break (timestamp-in-prefix breaks from seq 1: the stamp changes every call)
EXPECTED_FIRST_BREAK = {"timestamp-in-prefix": 1}
SIM_TTL_S = 300
REASONING_CHARS = {"max": 4000, "high": 2000, "medium": 1400, "low": 700}

_SYSTEM = "\n".join(
    f"Rule {i}: keep answers short, cite file paths, never run destructive commands without a dry run ({i * 7 % 13})."
    for i in range(1, 121)
)  # ~12k chars
_TOOLS = [
    ("read_file", "Read a file", {"path": {"type": "string"}}),
    ("run_shell", "Run a shell command", {"cmd": {"type": "string"}, "cwd": {"type": "string"}}),
    ("search_code", "Grep the repo", {"pattern": {"type": "string"}}),
]


def _filler(seed: str, n: int) -> str:
    out, i = [], 0
    while sum(map(len, out)) < n:
        out.append(hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:16] + " ")
        i += 1
    return "".join(out)[:n]


def _msg(role: str, *parts: Part) -> TraceMessage:
    return TraceMessage(role=role, parts=list(parts))


def _serialise(model: str, tools: list[TraceTool], system: str, messages: list[TraceMessage]) -> str:
    lines = [model, json.dumps([t.model_dump() for t in tools], sort_keys=True), system]
    for m in messages:
        body = "".join(
            f"<{p.type}>{p.text or ''}{json.dumps(p.arguments) if p.arguments else ''}{p.content or ''}"
            for p in m.parts
        )
        lines.append(f"{m.role}|{body}")
    return "\n".join(lines)


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def build_session(
    scenario: str,
    *,
    trace_id: str | None = None,
    model: str = "glm-5.3",
    effort: str | None = "high",
    turns: int = 7,
    t0: datetime | None = None,
    break_at: int = BREAK_AT,
    reasoning_chars: int | None = None,
) -> list[SwitchTrace]:
    """One agent task of ``turns`` model calls with the named scenario planted."""
    if scenario not in SCENARIOS:
        raise KeyError(f"unknown scenario {scenario!r}; one of {sorted(SCENARIOS)}")
    trace_id = trace_id or f"synthetic-{scenario}"
    t0 = t0 or datetime(2026, 10, 26, 9, 0, 0, tzinfo=UTC)
    rc = reasoning_chars if reasoning_chars is not None else REASONING_CHARS.get(effort or "high", 2000)
    base_tools = [
        TraceTool(name=n, description=d, parameters={"type": "object", "properties": p}) for n, d, p in _TOOLS
    ]

    # the "true" conversation: user, then (assistant reasoning+call, tool result) per turn
    reasoning = [_filler(f"{trace_id}:r{k}", rc) for k in range(turns)]
    assistants = [
        _msg(
            "assistant",
            Part(type="reasoning", text=reasoning[k]),
            Part(type="text", text=f"Step {k}: inspecting."),
            Part(type="tool_call", id=f"call_{k}", name="run_shell", arguments={"cmd": f"step {k}"}),
        )
        for k in range(turns)
    ]
    results = [
        _msg("tool", Part(type="tool_result", id=f"call_{k}", content=_filler(f"{trace_id}:t{k}", 3000)))
        for k in range(turns)
    ]
    user = _msg("user", Part(type="text", text="Fix the failing test in the repo and explain the change."))

    def ts(k: int) -> datetime:
        gap = timedelta(minutes=20) if scenario == "idle-gap" and k >= break_at else timedelta(0)
        return t0 + timedelta(seconds=20 * k) + gap

    calls: list[SwitchTrace] = []
    prev_text: str | None = None
    for k in range(turns):
        # request k = user + (assistant j, tool result j) for j < k
        msgs: list[TraceMessage] = [user]
        for j in range(k):
            msgs += [assistants[j].model_copy(deep=True), results[j]]
        system = _SYSTEM
        tools = list(base_tools)
        if scenario == "timestamp-in-prefix":
            system = f"Current time: {ts(k).strftime('%Y-%m-%dT%H:%M:%SZ')}\n" + _SYSTEM
        if scenario == "system-prompt-churn" and k >= break_at:
            system = _SYSTEM.replace("Rule 1:", "Rule 1 (policy v2, rolled out mid-session):", 1)
        if scenario == "tool-reorder" and k >= break_at:
            tools = list(reversed(tools))
        if scenario == "reasoning-edited" and k >= break_at:
            first = next(m for m in msgs if m.role == "assistant")
            for p in first.parts:
                if p.type == "reasoning":
                    p.text = (p.text or "")[:-37].replace("a", "A", 3)
        if scenario == "reasoning-dropped" and k >= break_at:
            for m in msgs:
                if m.role == "assistant":
                    m.parts = [p for p in m.parts if p.type != "reasoning"]
        if scenario == "history-compaction" and k >= break_at:
            summary = _msg("user", Part(type="text", text="Summary of earlier work: investigated, no change yet."))
            msgs = [summary, *msgs[3:]]
        text = _serialise(model, tools, system, msgs)
        input_tokens = len(text) // 4
        cache_read = 0
        if prev_text is not None:
            cache_read = _common_prefix(prev_text, text) // 4
            if (ts(k) - ts(k - 1)).total_seconds() > SIM_TTL_S:
                cache_read = 0
            if scenario == "host-eviction" and k == break_at:
                cache_read = 0
        resp = assistants[k]
        out_chars = sum(len(p.text or "") + len(json.dumps(p.arguments) if p.arguments else "") for p in resp.parts)
        calls.append(
            SwitchTrace(
                call_id=hashlib.sha256(f"{trace_id}/{k}".encode()).hexdigest()[:24],
                trace_id=trace_id,
                seq=k,
                ts_start=ts(k),
                task_type="synthetic-agent",
                source=TraceSource(kind="glmkit", ref=f"synthetic/{scenario}#{k}", adapter_version="cost-synth/1"),
                request=TraceRequest(
                    provider="zai",
                    model=model,
                    system=[Part(type="text", text=system)],
                    messages=msgs,
                    tools=tools,
                    params=TraceParams(reasoning_effort=effort),
                ),
                response=TraceResponse(messages=[resp], finish_reason="tool_calls"),
                usage=TraceUsage(
                    input_tokens_total=input_tokens,
                    cache_read_tokens=cache_read,
                    output_tokens_total=out_chars // 4,
                    reasoning_tokens=len(reasoning[k]) // 4,
                    source_convention="openai",
                ),
            )
        )
        prev_text = text
    return calls


def demo_traces() -> list[SwitchTrace]:
    """Every scenario once, plus the same healthy task at max / high / low effort for the verbosity panel."""
    out: list[SwitchTrace] = []
    for i, name in enumerate(SCENARIOS):
        out += build_session(name, t0=datetime(2026, 10, 26, 9, i, 0, tzinfo=UTC))
    for i, eff in enumerate(("max", "low")):
        for n in range(2):
            out += build_session(
                "healthy",
                trace_id=f"synthetic-effort-{eff}-{n}",
                effort=eff,
                t0=datetime(2026, 10, 26, 11, i * 10 + n, 0, tzinfo=UTC),
            )
    out += build_session(
        "healthy",
        trace_id="synthetic-flash-0",
        model="glm-5.3-flash",
        effort="high",
        t0=datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC),
    )
    return out


def demo_token_counts() -> list[dict[str, Any]]:
    """Synthetic GLM-vs-incumbent token counts from two toy tokenisers (chars per token differ by content type)."""
    samples = {
        "json": ('{"id": 1, "items": [' + ", ".join(f'{{"k": "v{i}"}}' for i in range(40)) + "]}", 2.9, 3.4),
        "code": ("def f(x):\n    return x + 1\n" * 30, 3.3, 3.5),
        "cjk": ("今天天气很好，我们去公园散步吧。" * 20, 1.3, 1.2),
        "prose": ("The quick brown fox jumps over the lazy dog. " * 30, 4.2, 4.3),
    }
    return [
        {
            "content_type": ct,
            "text": txt,
            "glm_tokens": round(len(txt) / g),
            "incumbent_tokens": round(len(txt) / inc),
            "synthetic": True,
        }
        for ct, (txt, g, inc) in samples.items()
    ]
