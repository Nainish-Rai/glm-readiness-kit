"""Langfuse observation exports (JSONL, JSON or CSV; one row per observation, ``GENERATION`` rows only).

ASSUMPTION (research/03-eval.md: exact export column names not verified against a real export): columns are
read under both snake_case and camelCase names (``trace_id``/``traceId``, ``usage_details``/``usageDetails``,
...). ``usage_details`` keys: ``input``, ``output``, ``total``, plus any of ``input_cache_read`` /
``cache_read_input_tokens`` / ``input_cached_tokens`` (read) and ``input_cache_creation`` /
``cache_creation_input_tokens`` (write). For Claude models ``input`` excludes cache (Anthropic convention) and is
converted; otherwise ``input`` is taken as the total. Langfuse ``cost_details.total`` -> ``cost_usd_reported``.
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
    TraceUsage,
    _openai_message,
    _text_parts,
)
from glmkit.traces.common import EPOCH, json_or_raw, ms_between, openai_messages, parse_ts, stable_id
from glmkit.traces.registry import Adapter, ImportContext, as_float, as_int, read_records, register_adapter

READ_KEYS = ("input_cache_read", "cache_read_input_tokens", "input_cached_tokens", "cache_read")
WRITE_KEYS = ("input_cache_creation", "cache_creation_input_tokens", "input_cache_write", "cache_write")


def pick(row: dict[str, Any], *names: str) -> Any:
    for n in names:
        if row.get(n) not in (None, ""):
            return json_or_raw(row[n])
    return None


def first_int(d: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for k in keys:
        if as_int(d.get(k)) is not None:
            return as_int(d.get(k))
    return None


def _usage(details: dict[str, Any], model: str) -> TraceUsage:
    if not details:
        return TraceUsage()
    inp = as_int(details.get("input"))
    read, write = first_int(details, READ_KEYS), first_int(details, WRITE_KEYS)
    anthropic = "claude" in model.lower() or "anthropic" in model.lower()
    total_in = inp
    if anthropic and inp is not None:
        total_in = inp + (read or 0) + (write or 0)
    return TraceUsage(
        input_tokens_total=total_in,
        cache_read_tokens=read,
        cache_write_tokens=write,
        output_tokens_total=as_int(details.get("output")),
        reasoning_tokens=first_int(details, ("output_reasoning", "reasoning_tokens", "reasoning")),
        source_convention="anthropic" if anthropic else "openai",
    )


def _messages(inp: Any) -> tuple[list[Part], list[TraceMessage]]:
    if isinstance(inp, dict):
        inp = inp.get("messages", inp)
    if isinstance(inp, str):
        return [], [TraceMessage(role="user", parts=[Part(type="text", text=inp)])]
    if isinstance(inp, list) and inp and isinstance(inp[0], dict) and "role" in inp[0]:
        return openai_messages(inp)
    return [], []


@register_adapter
class LangfuseAdapter(Adapter):
    kind = "langfuse"
    version = "0.1"
    title = "Langfuse observation export"
    formats = "jsonl, json, csv"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        for row_no, row in read_records(ctx):
            if not isinstance(row, dict):
                ctx.skip("row is not an object")
                continue
            otype = str(pick(row, "type") or "GENERATION").upper()
            if otype != "GENERATION":
                ctx.skip(f"ignored observation type: {otype}")
                continue
            model = str(pick(row, "model", "provided_model_name", "providedModelName") or "")
            system, messages = _messages(pick(row, "input"))
            output = pick(row, "output")
            out_msg: TraceMessage | None = None
            if isinstance(output, dict) and output.get("role"):
                out_msg = _openai_message(output)
            elif isinstance(output, dict) and output.get("content") is not None:
                out_msg = TraceMessage(role="assistant", parts=_text_parts(output.get("content")))
            elif isinstance(output, str):
                out_msg = TraceMessage(role="assistant", parts=[Part(type="text", text=output)])
            details = pick(row, "usage_details", "usageDetails") or {}
            costs = pick(row, "cost_details", "costDetails") or {}
            cost = as_float(costs.get("total")) if isinstance(costs, dict) else None
            if cost is None:
                cost = as_float(pick(row, "calculated_total_cost", "calculatedTotalCost", "total_cost", "totalCost"))
            meta = pick(row, "metadata")
            meta = meta if isinstance(meta, dict) else {}
            tags = pick(row, "tags") or []
            tags = (
                [str(t) for t in tags]
                if isinstance(tags, list)
                else [t.strip() for t in str(tags).split(",") if t.strip()]
            )
            mp = pick(row, "model_parameters", "modelParameters")
            mp = mp if isinstance(mp, dict) else {}
            start = pick(row, "start_time", "startTime")
            oid = str(pick(row, "id") or stable_id(ctx.path.name, row_no))
            tid = str(pick(row, "trace_id", "traceId") or oid)
            level = str(pick(row, "level") or "").upper()
            yield SwitchTrace(
                call_id=oid,
                trace_id=tid,
                parent_call_id=pick(row, "parent_observation_id", "parentObservationId"),
                ts_start=parse_ts(start) or EPOCH,
                task_type=ctx.task_type(tags=tags, meta=meta, model=model),
                source=TraceSource(kind="langfuse", ref=ctx.ref(row_no), adapter_version=self.version),
                request=TraceRequest(
                    provider=str(meta.get("provider") or "unknown"),
                    model=model,
                    system=system,
                    messages=messages,
                    params=TraceParams(
                        temperature=as_float(mp.get("temperature")),
                        top_p=as_float(mp.get("top_p")),
                        max_tokens=as_int(mp.get("max_tokens")),
                    ),
                ),
                response=TraceResponse(
                    messages=[out_msg] if out_msg and out_msg.parts else [],
                    error=str(pick(row, "status_message", "statusMessage") or "error") if level == "ERROR" else None,
                ),
                usage=_usage(details if isinstance(details, dict) else {}, model),
                latency=TraceLatency(
                    total_ms=ms_between(start, pick(row, "end_time", "endTime")),
                    ttft_ms=ms_between(start, pick(row, "completion_start_time", "completionStartTime")),
                ),
                cost_usd_reported=cost,
                privacy=TracePrivacy(content_present=bool(messages)),
            )
