"""Claude Code session JSONL (``~/.claude/projects/*/*.jsonl``). The format is undocumented: this adapter was
written against sessions with ``version`` 2.x and fails soft on everything it does not recognise.

* One API response is split across several ``assistant`` lines (one per content block), each repeating the same
  ``usage``. Lines are merged by ``message.id``; usage is counted once per id (summing per line over-counts).
* ``isSidechain: true`` lines are subagent traffic: tagged ``subagent`` in ``labels.tags``.
* ``usage.input_tokens`` excludes cache (Anthropic convention); converted to total-including-cache.
* The request is reconstructed from the earlier user/assistant lines of the same stream; the system prompt and
  tool definitions are not in the file, so replay is approximate.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from glmkit.contracts.trace import (
    Part,
    SwitchTrace,
    TraceLabels,
    TraceMessage,
    TraceParams,
    TracePrivacy,
    TraceRequest,
    TraceResponse,
    TraceSource,
    _text_parts,
)
from glmkit.traces.common import EPOCH, anthropic_usage, parse_ts, stable_id
from glmkit.traces.registry import Adapter, ImportContext, read_records, register_adapter

USAGE_INT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
STOP_MAP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


@dataclass
class _Call:
    msg_id: str
    row: int
    session: str
    sidechain: bool
    ts: Any
    model: str
    effort: str | None
    history: list[TraceMessage]
    parts: list[Part] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
    lines: int = 0


def _merge_usage(into: dict[str, Any], u: dict[str, Any]) -> None:
    """Every line of one response repeats the same usage; keep the max per field, never add."""
    for k in USAGE_INT_FIELDS:
        if isinstance(u.get(k), int):
            into[k] = max(into.get(k, 0), u[k])
    for k in ("output_tokens_details", "cache_creation"):
        if isinstance(u.get(k), dict):
            cur = into.setdefault(k, {})
            for kk, vv in u[k].items():
                if isinstance(vv, int):
                    cur[kk] = max(cur.get(kk, 0), vv)


@register_adapter
class ClaudeCodeAdapter(Adapter):
    kind = "claude_code"
    version = "0.1"
    title = "Claude Code session JSONL"
    formats = "jsonl"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        calls: dict[str, _Call] = {}
        histories: dict[tuple[str, bool], list[TraceMessage]] = {}
        for row_no, line in read_records(ctx):
            if not isinstance(line, dict):
                ctx.skip("row is not an object")
                continue
            kind = line.get("type")
            msg = line.get("message")
            if kind not in ("user", "assistant") or not isinstance(msg, dict):
                ctx.skip(f"ignored line type: {kind}")
                continue
            session = str(line.get("sessionId") or ctx.path.stem)
            side = bool(line.get("isSidechain"))
            hist = histories.setdefault((session, side), [])
            if kind == "user":
                hist.append(TraceMessage(role="user", parts=_text_parts(msg.get("content"))))
                continue
            mid = msg.get("id")
            if not mid:
                ctx.skip("assistant line without message.id")
                continue
            if msg.get("model") == "<synthetic>":
                ctx.skip("synthetic assistant message")
                continue
            call = calls.get(mid)
            if call is None:
                call = calls[mid] = _Call(
                    msg_id=str(mid),
                    row=row_no,
                    session=session,
                    sidechain=side,
                    ts=line.get("timestamp"),
                    model=str(msg.get("model") or ""),
                    effort=line.get("effort"),
                    history=list(hist),
                )
                shared = TraceMessage(role="assistant", parts=[])
                hist.append(shared)  # grows as later lines of the same response arrive
                call.parts = shared.parts
            call.lines += 1
            call.parts.extend(_text_parts(msg.get("content")))
            if isinstance(msg.get("usage"), dict):
                _merge_usage(call.usage, msg["usage"])
            call.stop_reason = msg.get("stop_reason") or call.stop_reason
        seq: dict[str, int] = {}
        for call in calls.values():
            n = seq[call.session] = seq.get(call.session, -1) + 1
            sidechain_tag = ["subagent"] if call.sidechain else []
            yield SwitchTrace(
                call_id=call.msg_id,
                trace_id=call.session,
                seq=n,
                ts_start=parse_ts(call.ts) or EPOCH,
                task_type=ctx.task_type(tags=sidechain_tag, model=call.model),
                source=TraceSource(
                    kind="claude_code",
                    ref=ctx.ref(call.row) + f"/{stable_id(call.msg_id)[:8]}",
                    adapter_version=self.version,
                ),
                request=TraceRequest(
                    provider="anthropic",
                    model=call.model,
                    messages=call.history,
                    params=TraceParams(reasoning_effort=call.effort, stream=True),
                ),
                response=TraceResponse(
                    messages=[TraceMessage(role="assistant", parts=call.parts)] if call.parts else [],
                    finish_reason=STOP_MAP.get(call.stop_reason or "", call.stop_reason),
                ),
                usage=anthropic_usage(call.usage),
                labels=TraceLabels(tags=sidechain_tag),
                privacy=TracePrivacy(content_present=bool(call.history)),
            )
