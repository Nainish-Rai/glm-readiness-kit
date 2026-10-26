"""OpenTelemetry GenAI spans (JSONL of spans, or OTLP/JSON ``resourceSpans``).

Attribute names follow open-telemetry/semantic-conventions-genai ``docs/gen-ai/client-inference.md`` at commit
``20259355b9631e3f032dcc099d1b5a2666818c42`` (2026-10-26). The conventions are in Development status.
``gen_ai.usage.input_tokens`` includes cached tokens (OpenAI convention). Content attributes are opt-in: spans
without ``gen_ai.input.messages`` become baseline-only rows (``content_present=false``) that cannot be replayed.
Only inference spans (``chat``, ``text_completion``, ``generate_content``) are imported; agent and tool spans
are counted and skipped.
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
    TraceTool,
    TraceUsage,
)
from glmkit.traces.common import EPOCH, json_or_raw, ms_between, parse_ts, stable_id
from glmkit.traces.registry import Adapter, ImportContext, as_float, as_int, read_records, register_adapter

INFERENCE_OPS = frozenset({"chat", "text_completion", "generate_content", "completion"})
SEMCONV_COMMIT = "20259355b9631e3f032dcc099d1b5a2666818c42"


def otlp_value(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return v[k]
    if "arrayValue" in v:
        return [otlp_value(x) for x in v["arrayValue"].get("values", [])]
    return v


def attrs_of(span: dict[str, Any]) -> dict[str, Any]:
    a = span.get("attributes") or {}
    if isinstance(a, list):
        return {x.get("key"): otlp_value(x.get("value")) for x in a if isinstance(x, dict)}
    return dict(a)


def iter_spans(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict) and "resourceSpans" in obj:
        for rs in obj["resourceSpans"]:
            for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
                yield from ss.get("spans", [])
    elif isinstance(obj, dict):
        yield obj


def otel_parts(parts: Any) -> list[Part]:
    out: list[Part] = []
    for p in parts or []:
        if isinstance(p, str):
            out.append(Part(type="text", text=p))
        elif not isinstance(p, dict):
            continue
        elif p.get("type") == "text":
            out.append(Part(type="text", text=p.get("content", p.get("text"))))
        elif p.get("type") == "tool_call":
            out.append(
                Part(type="tool_call", id=p.get("id"), name=p.get("name"), arguments=json_or_raw(p.get("arguments")))
            )
        elif p.get("type") in ("tool_call_response", "tool_result"):
            out.append(Part(type="tool_result", id=p.get("id"), content=p.get("response", p.get("content"))))
        elif p.get("type") in ("reasoning", "thinking"):
            out.append(Part(type="reasoning", text=p.get("content", p.get("text"))))
        else:
            out.append(Part(type=str(p.get("type", "unknown")), content=p))
    return out


def otel_messages(raw: Any) -> list[TraceMessage]:
    raw = json_or_raw(raw)
    if not isinstance(raw, list):
        return []
    return [
        TraceMessage(role=str(m.get("role", "user")), parts=otel_parts(m.get("parts", m.get("content"))))
        for m in raw
        if isinstance(m, dict)
    ]


@register_adapter
class OTelAdapter(Adapter):
    kind = "otel"
    version = f"0.1+semconv-genai@{SEMCONV_COMMIT[:12]}"
    title = "OpenTelemetry GenAI spans"
    formats = "jsonl, json"

    def parse(self, ctx: ImportContext) -> Iterator[SwitchTrace]:
        rows: list[SwitchTrace] = []
        for row_no, obj in read_records(ctx):
            for span in iter_spans(obj):
                a = attrs_of(span)
                op = a.get("gen_ai.operation.name")
                if not op:
                    ctx.skip("span without gen_ai.operation.name")
                    continue
                if op not in INFERENCE_OPS:
                    ctx.skip(f"non-inference operation: {op}")
                    continue
                start = span.get("start_time") or span.get("startTimeUnixNano") or span.get("start_time_unix_nano")
                end = span.get("end_time") or span.get("endTimeUnixNano") or span.get("end_time_unix_nano")
                model = str(a.get("gen_ai.request.model") or a.get("gen_ai.response.model") or "")
                inputs = otel_messages(a.get("gen_ai.input.messages"))
                outputs = otel_messages(a.get("gen_ai.output.messages"))
                system = otel_parts(json_or_raw(a.get("gen_ai.system_instructions")))
                tools_raw = json_or_raw(a.get("gen_ai.tool.definitions"))
                tools = (
                    [
                        TraceTool(
                            name=t.get("name", ""),
                            description=t.get("description"),
                            parameters=t.get("parameters") or {},
                        )
                        for t in tools_raw
                        if isinstance(t, dict)
                    ]
                    if isinstance(tools_raw, list)
                    else []
                )
                frs = json_or_raw(a.get("gen_ai.response.finish_reasons"))
                finish = frs[0] if isinstance(frs, list) and frs else (frs if isinstance(frs, str) else None)
                if not finish and isinstance(json_or_raw(a.get("gen_ai.output.messages")), list):
                    finish = next(
                        (
                            m.get("finish_reason")
                            for m in json_or_raw(a["gen_ai.output.messages"])
                            if isinstance(m, dict) and m.get("finish_reason")
                        ),
                        None,
                    )
                span_id = str(
                    span.get("span_id")
                    or span.get("spanId")
                    or a.get("gen_ai.response.id")
                    or stable_id(ctx.path.name, row_no)
                )
                trace_id = str(
                    span.get("trace_id") or span.get("traceId") or a.get("gen_ai.conversation.id") or span_id
                )
                status = span.get("status") or {}
                err = a.get("error.type") or (
                    status.get("message")
                    if isinstance(status, dict) and status.get("code") in (2, "ERROR", "STATUS_CODE_ERROR")
                    else None
                )
                rows.append(
                    SwitchTrace(
                        call_id=span_id,
                        trace_id=trace_id,
                        ts_start=parse_ts(start) or EPOCH,
                        task_type=ctx.task_type(
                            meta={k: v for k, v in a.items() if not k.startswith("gen_ai.")}, model=model
                        ),
                        source=TraceSource(kind="otel", ref=ctx.ref(row_no), adapter_version=self.version),
                        request=TraceRequest(
                            provider=str(a.get("gen_ai.provider.name") or "unknown"),
                            model=model,
                            system=system,
                            messages=inputs,
                            tools=tools,
                            params=TraceParams(
                                temperature=as_float(a.get("gen_ai.request.temperature")),
                                top_p=as_float(a.get("gen_ai.request.top_p")),
                                max_tokens=as_int(a.get("gen_ai.request.max_tokens")),
                                reasoning_effort=a.get("gen_ai.request.reasoning.level"),
                                stream=bool(a.get("gen_ai.request.stream", False)),
                            ),
                        ),
                        response=TraceResponse(
                            messages=outputs[:1] if outputs else [],
                            finish_reason=finish,
                            error=str(err) if err else None,
                        ),
                        usage=TraceUsage(
                            input_tokens_total=as_int(a.get("gen_ai.usage.input_tokens")),
                            cache_read_tokens=as_int(a.get("gen_ai.usage.cache_read.input_tokens")),
                            cache_write_tokens=as_int(a.get("gen_ai.usage.cache_write.input_tokens")),
                            output_tokens_total=as_int(a.get("gen_ai.usage.output_tokens")),
                            reasoning_tokens=as_int(a.get("gen_ai.usage.reasoning.output_tokens")),
                            source_convention="openai",
                        ),
                        latency=TraceLatency(total_ms=ms_between(start, end)),
                        privacy=TracePrivacy(content_present=bool(inputs)),
                    )
                )
        counters: dict[str, int] = {}
        for t in sorted(rows, key=lambda t: t.ts_start):
            t.seq = counters[t.trace_id] = counters.get(t.trace_id, -1) + 1
            yield t
