"""LiteLLM spend logs (``LiteLLM_SpendLogs`` rows as JSONL, JSON or CSV). These are the incumbent's cost truth
(``spend`` -> ``cost_usd_reported``) and usually carry no prompts: ``messages``/``response`` default to ``{}``
unless the operator enabled message logging, so such rows become baseline-only (``content_present=false``).
``cache_hit`` is LiteLLM's own cache flag, not provider prompt caching; cached/reasoning token detail is mined
from ``response.usage`` when present."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from glmkit.contracts.trace import (
    SwitchTrace,
    TraceLatency,
    TraceMessage,
    TracePrivacy,
    TraceRequest,
    TraceResponse,
    TraceSource,
    TraceUsage,
    _openai_message,
)
from glmkit.traces.common import EPOCH, json_or_raw, ms_between, openai_messages, openai_usage, parse_ts, stable_id
from glmkit.traces.registry import Adapter, ImportContext, as_float, as_int, read_records, register_adapter


def _tags(v: Any) -> list[str]:
    v = json_or_raw(v)
    if isinstance(v, list):
        return [str(t) for t in v]
    if isinstance(v, str) and v:
        return [t.strip() for t in v.split(",") if t.strip()]
    return []


@register_adapter
class LiteLLMAdapter(Adapter):
    kind = "litellm"
    version = "0.1"
    title = "LiteLLM spend logs"
    formats = "jsonl, json, csv"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        for row_no, row in read_records(ctx):
            if not isinstance(row, dict) or not (row.get("request_id") or row.get("model")):
                ctx.skip("not a spend-log row")
                continue
            messages_raw = json_or_raw(row.get("messages"))
            if isinstance(messages_raw, dict):
                messages_raw = messages_raw.get("messages") or []
            response = json_or_raw(row.get("response"))
            response = response if isinstance(response, dict) else {}
            system, messages = openai_messages(messages_raw if isinstance(messages_raw, list) else [])
            choice = (response.get("choices") or [{}])[0]
            out = _openai_message({"role": "assistant", **choice["message"]}) if choice.get("message") else None
            usage = openai_usage(response.get("usage")) if response.get("usage") else TraceUsage()
            usage = usage.model_copy(
                update={
                    "input_tokens_total": as_int(row.get("prompt_tokens")),
                    "output_tokens_total": as_int(row.get("completion_tokens")),
                    "source_convention": "openai",
                }
            )
            meta = json_or_raw(row.get("metadata"))
            meta = meta if isinstance(meta, dict) else {}
            rid = str(row.get("request_id") or stable_id(ctx.path.name, row_no))
            start = row.get("startTime") or row.get("start_time")
            end = row.get("endTime") or row.get("end_time")
            first = row.get("completionStartTime") or row.get("completion_start_time")
            total_ms = as_float(row.get("request_duration_ms")) or ms_between(start, end)
            model = str(row.get("model") or "")
            tags = _tags(row.get("request_tags"))
            yield SwitchTrace(
                call_id=rid,
                trace_id=str(row.get("session_id") or rid),
                ts_start=parse_ts(start) or EPOCH,
                task_type=ctx.task_type(tags=tags, meta=meta, model=model),
                source=TraceSource(kind="litellm", ref=ctx.ref(row_no), adapter_version=self.version),
                request=TraceRequest(
                    provider=str(row.get("custom_llm_provider") or "unknown"),
                    model=model,
                    system=system,
                    messages=messages,
                ),
                response=TraceResponse(
                    messages=[out] if isinstance(out, TraceMessage) and out.parts else [],
                    finish_reason=choice.get("finish_reason"),
                    error=None
                    if str(row.get("status") or "success").lower() in ("success", "")
                    else str(row["status"]),
                ),
                usage=usage,
                latency=TraceLatency(total_ms=total_ms, ttft_ms=ms_between(start, first)),
                cost_usd_reported=as_float(row.get("spend")),
                privacy=TracePrivacy(content_present=bool(messages)),
            )
