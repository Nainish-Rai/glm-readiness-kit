"""Canonical request -> protocol body, and protocol response -> ``NormalizedResponse``.

The canonical request is OpenAI-chat flavoured (``glmkit.contracts.case.CaseRequest``). Bridge, eval and
conformance all reuse these functions, so protocol knowledge lives in one place.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from glmkit.contracts.evidence import NormalizedResponse, NormToolCall, NormUsage, StreamMeta

ANTHROPIC_VERSION = "2023-06-01"
FINISH_NORM = {
    "stop": "stop",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "tool_use": "tool_calls",
    "length": "length",
    "max_tokens": "length",
}


def deep_merge(base: Any, over: Any) -> Any:
    """Recursively merge ``over`` into a copy of ``base``. A ``None`` value in ``over`` deletes the key."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if tools is None:
        return None
    out = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            out.append(t)
        else:
            out.append({"type": "function", "function": {k: v for k, v in t.items() if k != "type"}})
    return out


def to_openai_body(canon: dict[str, Any], *, model: str, stream: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"model": canon.get("model") or model, "messages": copy.deepcopy(canon.get("messages", []))}
    tools = _openai_tools(canon.get("tools"))
    if tools is not None:
        body["tools"] = tools
    for k in ("tool_choice", "max_tokens", "temperature", "top_p", "stop"):
        if canon.get(k) is not None:
            body[k] = copy.deepcopy(canon[k])
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    body = deep_merge(body, canon.get("params") or {})
    body = deep_merge(body, (canon.get("overrides") or {}).get("openai_chat") or {})
    for k in canon.get("omit") or []:
        body.pop(k, None)
    return body


def _anthropic_tool_choice(tc: Any) -> Any:
    if tc in (None, "auto"):
        return None if tc is None else {"type": "auto"}
    if tc == "none":
        return {"type": "none"}
    if tc == "required":
        return {"type": "any"}
    if isinstance(tc, dict) and tc.get("type") == "function":
        return {"type": "tool", "name": tc.get("function", {}).get("name")}
    return tc


def _blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return copy.deepcopy(content)


def to_anthropic_body(canon: dict[str, Any], *, model: str, stream: bool) -> dict[str, Any]:
    system: list[str] = []
    messages: list[dict[str, Any]] = []
    for m in canon.get("messages", []):
        role = m.get("role")
        if role in ("system", "developer"):
            system.append(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": m.get("content")}
            if messages and messages[-1]["role"] == "user" and messages[-1].get("_tool_results"):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block], "_tool_results": True})
            continue
        blocks: list[dict[str, Any]] = []
        if role == "assistant" and m.get("reasoning_content"):
            blocks.append({"type": "thinking", "thinking": m["reasoning_content"], "signature": ""})
        blocks += _blocks(m.get("content"))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            blocks.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": args})
        if isinstance(m.get("content"), str) and not m.get("tool_calls") and not m.get("reasoning_content"):
            messages.append({"role": role, "content": m["content"]})
        else:
            messages.append({"role": role, "content": blocks})
    for m in messages:
        m.pop("_tool_results", None)
    body: dict[str, Any] = {"model": canon.get("model") or model, "messages": messages}
    if system:
        body["system"] = "\n\n".join(system)
    if canon.get("max_tokens") is not None:
        body["max_tokens"] = canon["max_tokens"]
    if canon.get("tools") is not None:
        body["tools"] = []
        for t in _openai_tools(canon["tools"]) or []:
            fn = t["function"]
            tool = {
                "name": fn.get("name"),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            if fn.get("description"):
                tool["description"] = fn["description"]
            body["tools"].append(tool)
    tc = _anthropic_tool_choice(canon.get("tool_choice"))
    if tc is not None:
        body["tool_choice"] = tc
    for k in ("temperature", "top_p"):
        if canon.get(k) is not None:
            body[k] = canon[k]
    if canon.get("stop") is not None:
        body["stop_sequences"] = [canon["stop"]] if isinstance(canon["stop"], str) else list(canon["stop"])
    if stream:
        body["stream"] = True
    body = deep_merge(body, canon.get("params") or {})
    body = deep_merge(body, (canon.get("overrides") or {}).get("anthropic_messages") or {})
    for k in canon.get("omit") or []:
        body.pop(k, None)
    return body


def build_body(protocol: str, canon: dict[str, Any], *, model: str, stream: bool) -> dict[str, Any]:
    if protocol == "anthropic_messages":
        return to_anthropic_body(canon, model=model, stream=stream)
    return to_openai_body(canon, model=model, stream=stream)


# ------------------------------------------------------------------ response normalization


def _apply_error(n: NormalizedResponse, body: Any) -> None:
    if not isinstance(body, dict):
        return
    err = body.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        n.error_type = err.get("type") if isinstance(err.get("type"), str) else n.error_type
        n.error_message = err.get("message") if err.get("message") is not None else n.error_message
    elif "code" in body and ("msg" in body or "message" in body) and "choices" not in body:
        code = body.get("code")
        n.error_message = body.get("msg") or body.get("message")
    else:
        return
    if code is not None:
        n.error_code = str(code)
        n.error_code_type = "integer" if isinstance(code, int) and not isinstance(code, bool) else "string"


def _tool_call(
    index: int, id_: str | None, name: str | None, arguments: str | None, parsed: Any = None
) -> NormToolCall:
    tc = NormToolCall(index=index, id=id_, name=name, arguments=arguments)
    if parsed is not None:
        tc.arguments_parsed = parsed
    elif arguments is not None:
        try:
            tc.arguments_parsed = json.loads(arguments) if arguments.strip() else None
            if not arguments.strip():
                tc.parse_error = "empty arguments"
        except ValueError as e:
            tc.parse_error = f"InvalidJson: {e}"
    return tc


def _openai_usage(u: dict[str, Any] | None) -> NormUsage | None:
    if not isinstance(u, dict):
        return None
    return NormUsage(
        prompt_tokens=u.get("prompt_tokens"),
        completion_tokens=u.get("completion_tokens"),
        total_tokens=u.get("total_tokens"),
        cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens"),
        reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        source_convention="openai",
        raw=u,
    )


def _anthropic_usage(u: dict[str, Any] | None) -> NormUsage | None:
    """Anthropic input_tokens excludes cache reads/writes; convert to the total-including-cache convention."""
    if not isinstance(u, dict):
        return None
    inp, read, write = (
        u.get("input_tokens"),
        u.get("cache_read_input_tokens") or 0,
        u.get("cache_creation_input_tokens") or 0,
    )
    prompt = None if inp is None else inp + read + write
    out = u.get("output_tokens")
    return NormUsage(
        prompt_tokens=prompt,
        completion_tokens=out,
        total_tokens=None if prompt is None or out is None else prompt + out,
        cached_tokens=u.get("cache_read_input_tokens"),
        source_convention="anthropic",
        raw=u,
    )


def _finish(n: NormalizedResponse, raw: str | None) -> None:
    n.finish_reason = raw
    n.finish_reason_norm = FINISH_NORM.get(raw, raw) if raw else None


def normalize_openai(status: int | None, body: Any) -> NormalizedResponse:
    n = NormalizedResponse(http_status=status)
    _apply_error(n, body)
    if isinstance(body, dict) and isinstance(body.get("choices"), list) and body["choices"]:
        ch = body["choices"][0]
        msg = ch.get("message") or {}
        n.content = msg.get("content")
        n.reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        n.tool_calls = [
            _tool_call(
                i, tc.get("id"), (tc.get("function") or {}).get("name"), (tc.get("function") or {}).get("arguments")
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        _finish(n, ch.get("finish_reason"))
    if isinstance(body, dict):
        n.model = body.get("model")
        n.usage = _openai_usage(body.get("usage"))
    n.ok = status is not None and 200 <= status < 300 and n.error_code is None and n.error_message is None
    return n


def normalize_anthropic(status: int | None, body: Any) -> NormalizedResponse:
    n = NormalizedResponse(http_status=status)
    _apply_error(n, body)
    if isinstance(body, dict) and body.get("type") == "message":
        text, thinking = [], []
        for b in body.get("content") or []:
            t = b.get("type")
            if t == "text":
                text.append(b.get("text") or "")
            elif t == "thinking":
                thinking.append(b.get("thinking") or "")
            elif t == "tool_use":
                inp = b.get("input")
                n.tool_calls.append(
                    _tool_call(len(n.tool_calls), b.get("id"), b.get("name"), json.dumps(inp), parsed=inp)
                )
                if not isinstance(inp, dict):
                    n.tool_calls[-1].parse_error = f"SchemaMismatch: input is {type(inp).__name__}, not an object"
        n.content = "".join(text) if text else None
        n.reasoning = "".join(thinking) if thinking else None
        _finish(n, body.get("stop_reason"))
        n.model = body.get("model")
        n.usage = _anthropic_usage(body.get("usage"))
    n.ok = (
        status is not None
        and 200 <= status < 300
        and n.error_code is None
        and n.error_message is None
        and n.error_type is None
    )
    return n


class StreamAccumulator:
    """Reassembles a streamed response into a ``NormalizedResponse``. Tool-call fragments are concatenated per
    ``index`` (OpenAI) or per content block (Anthropic)."""

    def __init__(self, protocol: str, status: int | None) -> None:
        self.protocol = protocol
        self.n = NormalizedResponse(http_status=status, stream=StreamMeta())
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._tools: dict[int, dict[str, Any]] = {}
        self._blocks: dict[int, dict[str, Any]] = {}
        self._usage: dict[str, Any] = {}

    def add(self, event: str | None, data: Any) -> bool:
        """Feed one event; returns True when it carried model output (used for TTFT)."""
        meta = self.n.stream
        assert meta is not None
        meta.event_count += 1
        if data == "[DONE]":
            meta.done_seen = True
            return False
        if not isinstance(data, dict):
            meta.parse_errors += 1
            return False
        if "error" in data:
            _apply_error(self.n, data)
            return False
        return self._add_anthropic(event, data) if self.protocol == "anthropic_messages" else self._add_openai(data)

    def _add_openai(self, d: dict[str, Any]) -> bool:
        meta = self.n.stream
        assert meta is not None
        if d.get("model"):
            self.n.model = d["model"]
        if isinstance(d.get("usage"), dict):
            self._usage = d["usage"]
        out = False
        for ch in d.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("role"):
                meta.role_events += 1
            r = delta.get("reasoning_content") or delta.get("reasoning")
            if r:
                self._reasoning.append(r)
                out = True
            if delta.get("content"):
                self._content.append(delta["content"])
                out = True
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = self._tools.setdefault(idx, {"id": None, "name": None, "args": []})
                slot["id"] = slot["id"] or tc.get("id")
                fn = tc.get("function") or {}
                slot["name"] = slot["name"] or fn.get("name")
                if fn.get("arguments") is not None:
                    slot["args"].append(fn["arguments"])
                out = True
            if ch.get("finish_reason"):
                meta.finish_events += 1
                _finish(self.n, ch["finish_reason"])
        return out

    def _add_anthropic(self, event: str | None, d: dict[str, Any]) -> bool:
        meta = self.n.stream
        assert meta is not None
        t = d.get("type") or event
        if t == "message_start":
            msg = d.get("message") or {}
            self.n.model = msg.get("model")
            meta.role_events += 1
            meta.first_usage = msg.get("usage")
            self._usage = dict(msg.get("usage") or {})
        elif t == "content_block_start":
            self._blocks[d.get("index", 0)] = {**(d.get("content_block") or {}), "_json": []}
        elif t == "content_block_delta":
            b = self._blocks.setdefault(d.get("index", 0), {"type": "text", "_json": []})
            delta = d.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                b["text"] = (b.get("text") or "") + delta.get("text", "")
            elif dt == "thinking_delta":
                b["thinking"] = (b.get("thinking") or "") + delta.get("thinking", "")
            elif dt == "input_json_delta":
                b["_json"].append(delta.get("partial_json", ""))
            elif dt == "signature_delta":
                b["signature"] = delta.get("signature")
                return False
            return True
        elif t == "message_delta":
            if isinstance(d.get("usage"), dict):
                self._usage.update({k: v for k, v in d["usage"].items() if v is not None})
            sr = (d.get("delta") or {}).get("stop_reason")
            if sr:
                meta.finish_events += 1
                _finish(self.n, sr)
        elif t == "message_stop":
            meta.done_seen = True
        return False

    def finish(self) -> NormalizedResponse:
        n = self.n
        if self.protocol == "anthropic_messages":
            text, thinking = [], []
            for idx in sorted(self._blocks):
                b = self._blocks[idx]
                if b.get("type") == "text":
                    text.append(b.get("text") or "")
                elif b.get("type") == "thinking":
                    thinking.append(b.get("thinking") or "")
                elif b.get("type") == "tool_use":
                    raw = "".join(b["_json"]) if b["_json"] else json.dumps(b.get("input") or {})
                    n.tool_calls.append(_tool_call(len(n.tool_calls), b.get("id"), b.get("name"), raw))
            n.content = "".join(text) if text else None
            n.reasoning = "".join(thinking) if thinking else None
            n.usage = _anthropic_usage(self._usage) if self._usage else None
        else:
            n.content = "".join(self._content) if self._content else None
            n.reasoning = "".join(self._reasoning) if self._reasoning else None
            n.tool_calls = [
                _tool_call(i, s["id"], s["name"], "".join(s["args"])) for i, s in sorted(self._tools.items())
            ]
            n.usage = _openai_usage(self._usage) if self._usage else None
        status = n.http_status
        n.ok = status is not None and 200 <= status < 300 and n.error_code is None and n.error_message is None
        return n


def normalize(protocol: str, status: int | None, body: Any) -> NormalizedResponse:
    return normalize_anthropic(status, body) if protocol == "anthropic_messages" else normalize_openai(status, body)
