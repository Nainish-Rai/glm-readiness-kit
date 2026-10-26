"""Encode a ``Reply`` / ``ErrorReply`` in Z.ai's OpenAI-compatible or Anthropic-compatible wire format."""

from __future__ import annotations

import json
from typing import Any

from glmkit.mock.types import ErrorReply, MockContext, MockEvent, Reply

STOP_REASON = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


def _chunks(s: str, n: int) -> list[str]:
    n = max(1, n)
    return [s[i : i + n] for i in range(0, len(s), n)] if s else []


def error_body(ctx: MockContext, err: ErrorReply) -> dict[str, Any]:
    """Z.ai error body: ``{"error": {"code": "1210", "message": ...}}`` (code is a string, as in the docs example).
    Anthropic path: Anthropic's ``{"type": "error", "error": {"type", "message"}}`` plus the Z.ai ``code``
    (ASSUMED shape; cc-switch#6737 only reports 'HTTP 400 code 1210')."""
    if ctx.protocol == "anthropic_messages":
        e: dict[str, Any] = {"type": err.anthropic_type, "message": err.message}
        if err.code is not None:
            e["code"] = err.code
        return {"type": "error", "error": e}
    e = {"message": err.message}
    if err.code is not None:
        e = {"code": err.code, **e}
    return {"error": e}


# ------------------------------------------------------------------ OpenAI chat completions


def openai_usage(reply: Reply) -> dict[str, Any]:
    u = reply.usage
    return {
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "total_tokens": u.prompt_tokens + u.completion_tokens,
        "prompt_tokens_details": {"cached_tokens": u.cached_tokens},
    }


def openai_body(ctx: MockContext, reply: Reply) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": reply.content}
    if reply.reasoning is not None:
        msg["reasoning_content"] = reply.reasoning
    if reply.tool_calls:
        msg["tool_calls"] = [
            {"id": t.id, "type": "function", "index": i, "function": {"name": t.name, "arguments": t.arguments}}
            for i, t in enumerate(reply.tool_calls)
        ]
    return {
        "id": f"chatcmpl-mock-{reply.id}",
        "request_id": reply.id,
        "object": "chat.completion",
        "created": ctx.config.created,
        "model": reply.model,
        "choices": [{"index": 0, "finish_reason": reply.finish_reason, "message": msg}],
        "usage": openai_usage(reply),
    }


def openai_events(ctx: MockContext, reply: Reply) -> list[MockEvent]:
    n = ctx.config.chunk_chars
    base = {
        "id": f"chatcmpl-mock-{reply.id}",
        "object": "chat.completion.chunk",
        "created": ctx.config.created,
        "model": reply.model,
    }

    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> MockEvent:
        return MockEvent({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra})

    out = [chunk({"role": "assistant"})]
    out += [chunk({"reasoning_content": p}) for p in _chunks(reply.reasoning or "", n)]
    out += [chunk({"content": p}) for p in _chunks(reply.content or "", n)]
    tool_stream = bool(ctx.req and ctx.req.tool_stream)
    for i, t in enumerate(reply.tool_calls):
        head = {"index": i, "id": t.id, "type": "function", "function": {"name": t.name, "arguments": ""}}
        if tool_stream:  # tool_stream=true: unbuffered argument fragments, concatenated per index by clients
            out.append(chunk({"tool_calls": [head]}))
            out += [
                chunk({"tool_calls": [{"index": i, "function": {"arguments": p}}]}) for p in _chunks(t.arguments, n)
            ]
        else:  # default: the whole call arrives in one delta
            head["function"]["arguments"] = t.arguments
            out.append(chunk({"tool_calls": [head]}))
    out.append(chunk({}, reply.finish_reason, usage=openai_usage(reply)))
    out.append(MockEvent("[DONE]"))
    return out


# ------------------------------------------------------------------ Anthropic messages


def anthropic_usage(reply: Reply, *, final: bool = True) -> dict[str, Any]:
    """Anthropic convention: input_tokens excludes cache reads."""
    u = reply.usage
    return {
        "input_tokens": u.prompt_tokens - u.cached_tokens,
        "output_tokens": u.completion_tokens if final else 1,
        "cache_read_input_tokens": u.cached_tokens,
        "cache_creation_input_tokens": 0,
    }


def _tool_input(arguments: str) -> Any:
    try:
        return json.loads(arguments)
    except ValueError:
        return {"_raw": arguments}


def anthropic_body(ctx: MockContext, reply: Reply) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if reply.reasoning is not None:
        content.append({"type": "thinking", "thinking": reply.reasoning, "signature": reply.signature})
    if reply.content:
        content.append({"type": "text", "text": reply.content})
    for t in reply.tool_calls:
        content.append(
            {
                "type": "tool_use",
                "id": t.id.replace("call_", "toolu_", 1),
                "name": t.name,
                "input": _tool_input(t.arguments),
            }
        )
    return {
        "id": f"msg_mock_{reply.id}",
        "type": "message",
        "role": "assistant",
        "model": reply.model,
        "content": content,
        "stop_reason": STOP_REASON.get(reply.finish_reason, reply.finish_reason),
        "stop_sequence": None,
        "usage": anthropic_usage(reply),
    }


def anthropic_events(ctx: MockContext, reply: Reply) -> list[MockEvent]:
    n = ctx.config.chunk_chars

    def ev(name: str, data: dict[str, Any]) -> MockEvent:
        return MockEvent({"type": name, **data}, event=name)

    out = [
        ev(
            "message_start",
            {
                "message": {
                    "id": f"msg_mock_{reply.id}",
                    "type": "message",
                    "role": "assistant",
                    "model": reply.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": anthropic_usage(reply, final=False),
                }
            },
        ),
        ev("ping", {}),
    ]
    idx = 0
    if reply.reasoning is not None:
        out.append(
            ev(
                "content_block_start",
                {"index": idx, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            )
        )
        out += [
            ev("content_block_delta", {"index": idx, "delta": {"type": "thinking_delta", "thinking": p}})
            for p in _chunks(reply.reasoning, n)
        ]
        out.append(
            ev(
                "content_block_delta",
                {"index": idx, "delta": {"type": "signature_delta", "signature": reply.signature}},
            )
        )
        out.append(ev("content_block_stop", {"index": idx}))
        idx += 1
    if reply.content:
        out.append(ev("content_block_start", {"index": idx, "content_block": {"type": "text", "text": ""}}))
        out += [
            ev("content_block_delta", {"index": idx, "delta": {"type": "text_delta", "text": p}})
            for p in _chunks(reply.content, n)
        ]
        out.append(ev("content_block_stop", {"index": idx}))
        idx += 1
    for t in reply.tool_calls:
        tid = t.id.replace("call_", "toolu_", 1)
        out.append(
            ev(
                "content_block_start",
                {"index": idx, "content_block": {"type": "tool_use", "id": tid, "name": t.name, "input": {}}},
            )
        )
        out += [
            ev("content_block_delta", {"index": idx, "delta": {"type": "input_json_delta", "partial_json": p}})
            for p in _chunks(t.arguments, n)
        ]
        out.append(ev("content_block_stop", {"index": idx}))
        idx += 1
    out.append(
        ev(
            "message_delta",
            {
                "delta": {
                    "stop_reason": STOP_REASON.get(reply.finish_reason, reply.finish_reason),
                    "stop_sequence": None,
                },
                "usage": anthropic_usage(reply),
            },
        )
    )
    out.append(ev("message_stop", {}))
    return out


def encode_body(ctx: MockContext, reply: Reply) -> dict[str, Any]:
    return anthropic_body(ctx, reply) if ctx.protocol == "anthropic_messages" else openai_body(ctx, reply)


def encode_events(ctx: MockContext, reply: Reply) -> list[MockEvent]:
    return anthropic_events(ctx, reply) if ctx.protocol == "anthropic_messages" else openai_events(ctx, reply)


def sse_bytes(e: MockEvent) -> bytes:
    data = e.data if isinstance(e.data, str) else json.dumps(e.data, ensure_ascii=False)
    head = f"event: {e.event}\n" if e.event else ""
    return f"{head}data: {data}\n\n".encode()
