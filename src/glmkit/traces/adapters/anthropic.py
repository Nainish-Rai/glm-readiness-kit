"""Anthropic Messages logs: JSONL of ``{"request": {...}, "response": {...}}`` (or a bare Messages response).

``usage.input_tokens`` EXCLUDES cache reads and writes; the adapter converts to the total-including-cache
convention (input + cache_read + cache_creation). Rows repeating a response ``id`` (log retries, duplicated
shipping) are dropped, so summing the output cannot double count a call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from glmkit.contracts.trace import (
    Part,
    SwitchTrace,
    TraceLatency,
    TraceMessage,
    TraceParams,
    TracePrivacy,
    TraceRequest,
    TraceResponse,
    TraceSource,
    _text_parts,
)
from glmkit.traces.common import EPOCH, anthropic_messages, anthropic_tools, anthropic_usage, parse_ts, stable_id
from glmkit.traces.registry import Adapter, ImportContext, as_float, read_records, register_adapter

STOP_MAP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


@register_adapter
class AnthropicAdapter(Adapter):
    kind = "anthropic"
    version = "0.1"
    title = "Anthropic Messages logs"
    formats = "jsonl, json"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        seen: set[str] = set()
        for row_no, row in read_records(ctx):
            if not isinstance(row, dict):
                ctx.skip("row is not an object")
                continue
            resp: dict[str, Any] = (
                row.get("response")
                if isinstance(row.get("response"), dict)
                else (row if row.get("type") == "message" else {})
            )
            if not resp:
                ctx.skip("no Messages response object")
                continue
            rid = str(resp.get("id") or stable_id(ctx.path.name, row_no))
            if rid in seen:
                ctx.skip("duplicate response id")
                continue
            seen.add(rid)
            req: dict[str, Any] = row.get("request") if isinstance(row.get("request"), dict) else {}
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else (req.get("metadata") or {})
            system, messages = anthropic_messages(req.get("system"), req.get("messages") or [])
            out_parts: list[Part] = _text_parts(resp.get("content"))
            model = str(req.get("model") or resp.get("model") or "")
            sr = resp.get("stop_reason")
            thinking = req.get("thinking") if isinstance(req.get("thinking"), dict) else {}
            effort = (req.get("output_config") or {}).get("effort") or thinking.get("type")
            yield SwitchTrace(
                call_id=rid,
                trace_id=str(row.get("trace_id") or meta.get("session_id") or rid),
                ts_start=parse_ts(row.get("timestamp") or row.get("created_at")) or EPOCH,
                task_type=ctx.task_type(tags=list(meta.get("tags") or []), meta=meta, model=model),
                source=TraceSource(kind="anthropic", ref=ctx.ref(row_no), adapter_version=self.version),
                request=TraceRequest(
                    provider="anthropic",
                    model=model,
                    system=system,
                    messages=messages,
                    tools=anthropic_tools(req.get("tools")),
                    params=TraceParams(
                        temperature=as_float(req.get("temperature")),
                        top_p=as_float(req.get("top_p")),
                        max_tokens=req.get("max_tokens"),
                        reasoning_effort=effort,
                        stream=bool(req.get("stream", False)),
                    ),
                ),
                response=TraceResponse(
                    messages=[TraceMessage(role="assistant", parts=out_parts)] if out_parts else [],
                    finish_reason=STOP_MAP.get(sr, sr),
                ),
                usage=anthropic_usage(resp.get("usage")),
                latency=TraceLatency(total_ms=as_float(row.get("latency_ms"))),
                privacy=TracePrivacy(content_present=bool(messages)),
            )
