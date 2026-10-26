"""SwitchTrace v0 (research/03-eval.md §2): one JSONL line per model call.

Input tokens are stored in the OpenAI/OTel convention (total, cache included) with explicit cache fields.
``parts[]`` mirrors OTel ``gen_ai.input.messages`` so OTel imports are near-identity.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from glmkit.contracts.evidence import Exchange

TRACE_SCHEMA_ID = "switch-eval.call/0"


class _Open(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TraceSource(_Open):
    kind: Literal["otel", "langfuse", "openai", "anthropic", "litellm", "claude_code", "golden", "glmkit"]
    ref: str = Field(description="file#row/id or run_id/item/exchange")
    adapter_version: str


class Part(BaseModel):
    """text | tool_call | tool_result | reasoning. Extra keys allowed for forward compatibility."""

    model_config = ConfigDict(extra="allow")
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    arguments: Any = None
    content: Any = None


class TraceMessage(_Open):
    role: str
    parts: list[Part] = Field(default_factory=list)


class TraceTool(_Open):
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class TraceParams(_Open):
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    response_format: Any = None
    stream: bool = False


class TraceRequest(_Open):
    provider: str
    model: str
    system: list[Part] = Field(default_factory=list)
    messages: list[TraceMessage] = Field(default_factory=list)
    tools: list[TraceTool] = Field(default_factory=list)
    params: TraceParams = Field(default_factory=TraceParams)


class TraceResponse(_Open):
    messages: list[TraceMessage] = Field(default_factory=list)
    finish_reason: str | None = None
    error: str | None = None


class TraceUsage(_Open):
    input_tokens_total: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens_total: int | None = None
    reasoning_tokens: int | None = None
    source_convention: Literal["openai", "anthropic"] | None = None


class TraceLatency(_Open):
    total_ms: float | None = None
    ttft_ms: float | None = None


class TraceLabels(_Open):
    expected: Any = None
    checks: list[Any] = Field(default_factory=list)
    human_score: float | None = None
    user_feedback: Any = None
    tags: list[str] = Field(default_factory=list, description="Optional free tags, e.g. 'subagent' (importers)")


class TracePrivacy(_Open):
    content_present: bool = True
    redacted: bool = False


class SwitchTrace(_Open):
    schema_: Literal["switch-eval.call/0"] = Field(default=TRACE_SCHEMA_ID, alias="schema")
    call_id: str
    trace_id: str
    parent_call_id: str | None = None
    seq: int = 0
    ts_start: datetime
    task_type: str | None = None
    source: TraceSource
    request: TraceRequest
    response: TraceResponse = Field(default_factory=TraceResponse)
    usage: TraceUsage = Field(default_factory=TraceUsage)
    latency: TraceLatency = Field(default_factory=TraceLatency)
    cost_usd_reported: float | None = None
    labels: TraceLabels = Field(default_factory=TraceLabels)
    privacy: TracePrivacy = Field(default_factory=TracePrivacy)

    def to_jsonl(self) -> str:
        return self.model_dump_json(by_alias=True)


# ------------------------------------------------------------------ conversion from an Exchange


def _text_parts(content: Any) -> list[Part]:
    if content is None:
        return []
    if isinstance(content, str):
        return [Part(type="text", text=content)]
    parts: list[Part] = []
    for block in content if isinstance(content, list) else [content]:
        if not isinstance(block, dict):
            parts.append(Part(type="text", text=str(block)))
        elif block.get("type") == "text":
            parts.append(Part(type="text", text=block.get("text")))
        elif block.get("type") == "tool_use":
            parts.append(
                Part(type="tool_call", id=block.get("id"), name=block.get("name"), arguments=block.get("input"))
            )
        elif block.get("type") == "tool_result":
            parts.append(Part(type="tool_result", id=block.get("tool_use_id"), content=block.get("content")))
        elif block.get("type") in ("thinking", "redacted_thinking"):
            parts.append(Part(type="reasoning", text=block.get("thinking")))
        else:
            parts.append(Part(type=str(block.get("type", "unknown")), content=block))
    return parts


def _openai_message(m: dict[str, Any]) -> TraceMessage:
    parts = []
    if m.get("reasoning_content"):
        parts.append(Part(type="reasoning", text=m["reasoning_content"]))
    if m.get("role") == "tool":
        parts.append(Part(type="tool_result", id=m.get("tool_call_id"), content=m.get("content")))
    else:
        parts += _text_parts(m.get("content"))
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function", {})
        args: Any = fn.get("arguments")
        if isinstance(args, str):
            with contextlib.suppress(ValueError):
                args = json.loads(args)
        parts.append(Part(type="tool_call", id=tc.get("id"), name=fn.get("name"), arguments=args))
    return TraceMessage(role=str(m.get("role", "user")), parts=parts)


def exchange_to_trace(
    ex: Exchange, *, trace_id: str, seq: int, ts_start: datetime, ref: str, provider: str, task_type: str | None = None
) -> SwitchTrace:
    """Build a SwitchTrace line from a recorded exchange (request body is already redacted)."""
    from glmkit import __version__

    body = ex.request.body if isinstance(ex.request.body, dict) else {}
    system: list[Part] = []
    messages: list[TraceMessage] = []
    tools: list[TraceTool] = []
    if ex.protocol == "anthropic_messages":
        system = _text_parts(body.get("system"))
        messages = [
            TraceMessage(role=m.get("role", "user"), parts=_text_parts(m.get("content")))
            for m in body.get("messages", [])
        ]
        tools = [
            TraceTool(name=t.get("name", ""), description=t.get("description"), parameters=t.get("input_schema") or {})
            for t in body.get("tools") or []
        ]
        effort = (body.get("output_config") or {}).get("effort") or body.get("reasoning_effort")
    else:
        for m in body.get("messages", []):
            if m.get("role") in ("system", "developer"):
                system += _text_parts(m.get("content"))
            else:
                messages.append(_openai_message(m))
        for t in body.get("tools") or []:
            fn = t.get("function", t)
            tools.append(
                TraceTool(
                    name=fn.get("name", ""), description=fn.get("description"), parameters=fn.get("parameters") or {}
                )
            )
        effort = body.get("reasoning_effort")

    n = ex.normalized
    out_parts: list[Part] = []
    if n.reasoning:
        out_parts.append(Part(type="reasoning", text=n.reasoning))
    if n.content:
        out_parts.append(Part(type="text", text=n.content))
    for tc in n.tool_calls:
        out_parts.append(
            Part(
                type="tool_call",
                id=tc.id,
                name=tc.name,
                arguments=tc.arguments_parsed if tc.parse_error is None else tc.arguments,
            )
        )
    u = n.usage
    usage = TraceUsage()
    if u:
        usage = TraceUsage(
            input_tokens_total=u.prompt_tokens,
            cache_read_tokens=u.cached_tokens,
            output_tokens_total=u.completion_tokens,
            reasoning_tokens=u.reasoning_tokens,
            source_convention=u.source_convention,
        )
    error = None
    if not n.ok:
        error = ex.transport_error or f"{n.http_status} {n.error_code or ''} {n.error_message or ''}".strip()
    call_id = hashlib.sha256(f"{ref}".encode()).hexdigest()[:24]
    return SwitchTrace(
        call_id=call_id,
        trace_id=trace_id,
        seq=seq,
        ts_start=ts_start,
        task_type=task_type,
        source=TraceSource(kind="glmkit", ref=ref, adapter_version=__version__),
        request=TraceRequest(
            provider=provider,
            model=str(body.get("model", "")),
            system=system,
            messages=messages,
            tools=tools,
            params=TraceParams(
                temperature=body.get("temperature"),
                top_p=body.get("top_p"),
                max_tokens=body.get("max_tokens"),
                reasoning_effort=effort,
                response_format=body.get("response_format"),
                stream=bool(body.get("stream", False)),
            ),
        ),
        response=TraceResponse(
            messages=[TraceMessage(role="assistant", parts=out_parts)] if out_parts else [],
            finish_reason=n.finish_reason_norm or n.finish_reason or ("error" if error else None),
            error=error,
        ),
        usage=usage,
        latency=TraceLatency(total_ms=ex.timing.total_ms, ttft_ms=ex.timing.ttft_ms),
    )
