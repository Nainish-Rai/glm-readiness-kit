"""OpenAI Chat Completions logs: JSONL of ``{"request": {...}, "response": {...}}`` (or a bare response/stored
completion). ``prompt_tokens`` already includes cached tokens, so usage is taken as-is."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from glmkit.contracts.trace import (
    SwitchTrace,
    TraceLatency,
    TraceMessage,
    TraceParams,
    TracePrivacy,
    TraceRequest,
    TraceResponse,
    TraceSource,
    _openai_message,
)
from glmkit.traces.common import EPOCH, openai_messages, openai_tools, openai_usage, parse_ts, stable_id
from glmkit.traces.registry import Adapter, ImportContext, as_float, read_records, register_adapter

REQUEST_KEYS = (
    "model",
    "messages",
    "tools",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "reasoning_effort",
    "response_format",
    "stream",
)


@register_adapter
class OpenAIAdapter(Adapter):
    kind = "openai"
    version = "0.1"
    title = "OpenAI Chat Completions logs"
    formats = "jsonl, json"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        for row_no, row in read_records(ctx):
            if not isinstance(row, dict):
                ctx.skip("row is not an object")
                continue
            resp: dict[str, Any] = (
                row.get("response") if isinstance(row.get("response"), dict) else (row if "choices" in row else {})
            )
            if not resp:
                ctx.skip("no response object")
                continue
            req: dict[str, Any] = (
                row.get("request")
                if isinstance(row.get("request"), dict)
                else {k: row[k] for k in REQUEST_KEYS if k in row}
            )
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            system, messages = openai_messages(req.get("messages") or [])
            choice = (resp.get("choices") or [{}])[0]
            out_msg = choice.get("message") or {}
            out = _openai_message({"role": "assistant", **out_msg}) if out_msg else None
            call_id = str(resp.get("id") or stable_id(ctx.path.name, row_no))
            model = str(req.get("model") or resp.get("model") or "")
            yield SwitchTrace(
                call_id=call_id,
                trace_id=str(row.get("trace_id") or meta.get("session_id") or call_id),
                seq=0,
                ts_start=parse_ts(row.get("timestamp") or resp.get("created")) or EPOCH,
                task_type=ctx.task_type(tags=list(meta.get("tags") or []), meta=meta, model=model),
                source=TraceSource(kind="openai", ref=ctx.ref(row_no), adapter_version=self.version),
                request=TraceRequest(
                    provider="openai",
                    model=model,
                    system=system,
                    messages=messages,
                    tools=openai_tools(req.get("tools")),
                    params=TraceParams(
                        temperature=as_float(req.get("temperature")),
                        top_p=as_float(req.get("top_p")),
                        max_tokens=req.get("max_completion_tokens") or req.get("max_tokens"),
                        reasoning_effort=req.get("reasoning_effort"),
                        response_format=req.get("response_format"),
                        stream=bool(req.get("stream", False)),
                    ),
                ),
                response=TraceResponse(
                    messages=[out] if isinstance(out, TraceMessage) and out.parts else [],
                    finish_reason=choice.get("finish_reason"),
                    error=None,
                ),
                usage=openai_usage(resp.get("usage")),
                latency=TraceLatency(total_ms=as_float(row.get("latency_ms"))),
                privacy=TracePrivacy(content_present=bool(messages)),
            )
