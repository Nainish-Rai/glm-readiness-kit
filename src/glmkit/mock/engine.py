"""Deterministic behaviour of the mock GLM server: request parsing, the documented validation rules
(research/01-conformance.md §2.3-§2.5) and reply generation.

Default behaviour is the *documented, correct* GLM behaviour. Known upstream bugs are only reproduced by
named defects (``glmkit.mock.defects``). Where the docs are silent, the choice made here is marked ASSUMED
and is to be confirmed by the live verification run (issue 16).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from glmkit.mock.types import (
    CanonRequest,
    ErrorReply,
    MockContext,
    Msg,
    Reply,
    Tool,
    ToolCallOut,
    Usage,
)

# model -> forced thinking? / allowed reasoning_effort on the standard API (None = no restriction)
MODELS: dict[str, dict[str, Any]] = {
    "glm-5.3": {"forced_thinking": True, "api_efforts": {"low", "high", "max"}},
    "glm-5.3-flash": {"forced_thinking": True, "api_efforts": {"low", "high", "max"}},
    "glm-5.3-flashx": {"forced_thinking": True, "api_efforts": {"low", "high", "max"}},
    "glm-5.3-flashx-legal": {"forced_thinking": True, "api_efforts": {"low", "high", "max"}},
    "glm-5.2": {"forced_thinking": False, "api_efforts": None},
    "glm-5.1": {"forced_thinking": False, "api_efforts": None},
    "glm-5": {"forced_thinking": False, "api_efforts": None},
    "glm-4.7": {"forced_thinking": True, "api_efforts": None},
    "glm-4.6": {"forced_thinking": False, "api_efforts": None},
}
DISABLED_VALUES = {"disabled", "false", "none", "off"}
FORCED_THINKING_MSG = "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。"
PLAN_EFFORT_MAP = {
    "none": "low",
    "minimal": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}
GLM52_EFFORT_MAP = {
    "none": None,
    "minimal": None,
    "low": "high",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}
MAX_OUTPUT_TOKENS = 131_072
MAX_TOOLS = 128


def toks(s: str | None) -> int:
    """Deterministic token estimate: ceil(chars / 4)."""
    return math.ceil(len(s) / 4) if s else 0


def _sha(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _base_model(model: str) -> str:
    return re.sub(r"\[[^\]]*\]$", "", model)  # 'glm-5.3[1m]' -> 'glm-5.3'


# ------------------------------------------------------------------ request parsing


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def _parse_openai(body: dict[str, Any]) -> CanonRequest:
    msgs: list[Msg] = []
    system = []
    for m in body.get("messages") or []:
        role = m.get("role", "user")
        if role in ("system", "developer"):
            system.append(_text_of(m.get("content")))
        elif role == "tool":
            msgs.append(
                Msg(role="tool", tool_results=[{"id": m.get("tool_call_id"), "content": _text_of(m.get("content"))}])
            )
        else:
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": fn.get("arguments")})
            msgs.append(
                Msg(role=role, text=_text_of(m.get("content")), reasoning=m.get("reasoning_content"), tool_calls=calls)
            )
    tools = []
    for t in body.get("tools") or []:
        fn = t.get("function") or t
        tools.append(
            Tool(name=fn.get("name", ""), description=fn.get("description", ""), parameters=fn.get("parameters") or {})
        )
    tc = body.get("tool_choice", "auto")
    if isinstance(tc, dict):
        tc = {"name": (tc.get("function") or {}).get("name")}
    thinking = body.get("thinking")
    stop = body.get("stop")
    return CanonRequest(
        model=body.get("model"),
        messages=msgs,
        system="\n".join(system),
        tools=tools,
        tool_choice=tc,
        thinking_type=str(thinking.get("type")).lower() if isinstance(thinking, dict) and "type" in thinking else None,
        effort=body.get("reasoning_effort"),
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        stop=[stop] if isinstance(stop, str) else list(stop or []),
        stream=bool(body.get("stream")),
        tool_stream=bool(body.get("tool_stream")),
    )


def _parse_anthropic(body: dict[str, Any]) -> CanonRequest:
    msgs: list[Msg] = []
    for m in body.get("messages") or []:
        content = m.get("content")
        if isinstance(content, str):
            msgs.append(Msg(role=m.get("role", "user"), text=content))
            continue
        msg = Msg(role=m.get("role", "user"))
        for b in content or []:
            t = b.get("type")
            if t == "text":
                msg.text += b.get("text", "")
            elif t == "thinking":
                msg.reasoning = (msg.reasoning or "") + b.get("thinking", "")
            elif t == "tool_use":
                msg.tool_calls.append({"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input")})
            elif t == "tool_result":
                msg.tool_results.append({"id": b.get("tool_use_id"), "content": _text_of(b.get("content"))})
        if msg.tool_results and not msg.text:
            msg.role = "tool"
        msgs.append(msg)
    tools = [
        Tool(name=t.get("name", ""), description=t.get("description", ""), parameters=t.get("input_schema") or {})
        for t in body.get("tools") or []
    ]
    tc_raw = body.get("tool_choice") or {"type": "auto"}
    tc: Any = {"auto": "auto", "none": "none", "any": "required"}.get(tc_raw.get("type"), "auto")
    if tc_raw.get("type") == "tool":
        tc = {"name": tc_raw.get("name")}
    thinking = body.get("thinking")
    effort = (body.get("output_config") or {}).get("effort") or body.get("reasoning_effort")
    system = body.get("system")
    return CanonRequest(
        model=body.get("model"),
        messages=msgs,
        system=_text_of(system),
        tools=tools,
        tool_choice=tc,
        thinking_type=str(thinking.get("type")).lower() if isinstance(thinking, dict) and "type" in thinking else None,
        effort=effort,
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        stop=list(body.get("stop_sequences") or []),
        stream=bool(body.get("stream")),
    )


def parse_request(protocol: str, body: dict[str, Any]) -> CanonRequest:
    return _parse_anthropic(body) if protocol == "anthropic_messages" else _parse_openai(body)


# ------------------------------------------------------------------ validation (documented behaviour)


def _err(status: int, code: str | None, message: str, reason: str, atype: str = "invalid_request_error") -> ErrorReply:
    return ErrorReply(status=status, code=code, message=message, anthropic_type=atype, reason=reason)


def check_auth(ctx: MockContext) -> ErrorReply | None:
    want = ctx.config.require_key
    if not want:
        return None
    auth = ctx.headers.get("authorization", "")
    got = auth[7:] if auth.lower().startswith("bearer ") else ctx.headers.get("x-api-key")
    if not got:
        return _err(
            401, "1001", "Authentication parameter not received in Header", "auth_missing", "authentication_error"
        )
    if got != want:
        return _err(401, "1000", "Authentication Failed", "auth_failed", "authentication_error")
    return None


def _has_image(body: dict[str, Any]) -> bool:
    return any(
        isinstance(b, dict) and b.get("type") in ("image_url", "image")
        for m in body.get("messages") or []
        if isinstance(m, dict) and isinstance(m.get("content"), list)
        for b in m["content"]
    )


def validate(ctx: MockContext) -> ErrorReply | None:
    """Apply GLM's documented request rules; on success resolve ``ctx.thinking`` / ``ctx.effort``."""
    body = ctx.body
    if not isinstance(body, dict):
        return _err(400, "1210", "Invalid API parameter, please check the documentation.", "bad_json")
    req = ctx.req = parse_request(ctx.protocol, body)
    if not req.model:
        return _err(400, "1213", "The parameter model was not received normally.", "missing_model")
    model = _base_model(req.model)
    if model not in MODELS:
        return _err(400, "1211", "Unknown Model, please check the model code.", "unknown_model", "not_found_error")
    if not req.messages:
        return _err(400, "1213", "The parameter messages was not received normally.", "missing_messages")
    if _has_image(body) and not model.startswith("glm-5.3-flash"):  # MM-001: text-only models reject images
        return _err(
            400,
            "1214",
            "The parameter messages is invalid: image input is not supported by this model.",
            "image_unsupported",
        )
    if ctx.protocol == "anthropic_messages" and req.max_tokens is None:
        # Anthropic Messages requires max_tokens. ASSUMED: 400 invalid_request_error (zai-coding-plugins#15 b1).
        return _err(400, "1213", "max_tokens: Field required", "missing_max_tokens")
    if req.temperature is not None and not 0 <= req.temperature <= 1:
        return _err(400, "1214", "The parameter temperature is invalid.", "bad_temperature")
    if req.top_p is not None and not 0.01 <= req.top_p <= 1:
        return _err(400, "1214", "The parameter top_p is invalid.", "bad_top_p")
    if req.max_tokens is not None and req.max_tokens > MAX_OUTPUT_TOKENS:
        return _err(400, "1214", "The parameter max_tokens is invalid.", "bad_max_tokens")
    if len(req.tools) > MAX_TOOLS:
        return _err(400, "1214", "The parameter tools is invalid: at most 128 functions.", "too_many_tools")

    spec = MODELS[model]
    plan = ctx.endpoint_class == "plan"
    disabled = req.thinking_type in DISABLED_VALUES
    effort = str(req.effort).lower() if req.effort is not None else None
    if spec["forced_thinking"] and disabled:
        if plan:
            # Coding Plan: thinking.type in {false, disabled, none, off} -> low; request continues (devpack docs).
            ctx.notes.append("thinking disabled -> effort low (plan)")
            ctx.thinking, ctx.effort = True, PLAN_EFFORT_MAP.get(effort or "low", "max")
            return None
        return _err(400, "1210", FORCED_THINKING_MSG, "forced_thinking")
    if effort is not None and spec["api_efforts"] is not None:
        if plan:
            ctx.effort = PLAN_EFFORT_MAP.get(effort, "max")  # unknown strings -> max
        elif effort not in spec["api_efforts"]:
            # Docs: "any other input will result in an error". ASSUMED code 1214 (parameter invalid).
            return _err(400, "1214", "The parameter reasoning_effort is invalid; use low, high or max.", "bad_effort")
        else:
            ctx.effort = effort
    elif effort is not None and model == "glm-5.2":
        ctx.effort = GLM52_EFFORT_MAP.get(effort, "max")
    else:
        ctx.effort = effort or "max"
    if spec["forced_thinking"]:
        ctx.thinking = True
    else:
        ctx.thinking = not disabled and (ctx.effort is not None)
    if ctx.thinking and ctx.effort is None:
        ctx.effort = "max"
    return None


# ------------------------------------------------------------------ scripted rules


def match_rule(ctx: MockContext) -> dict[str, Any] | None:
    """Rules: ``{name, match: {model, protocol, endpoint_class, last_user_regex, has_tools, header: {k: v}},
    reply: {...} | error: {status, code, message, anthropic_type}}``. ``X-GLMKit-Scenario: <name>`` forces a rule."""
    rules = ctx.config.rules
    forced = ctx.headers.get("x-glmkit-scenario")
    if forced:
        for r in rules:
            if r.get("name") == forced:
                return r
        return None
    req = ctx.req
    last_user = next((m.text for m in reversed(req.messages) if m.role == "user"), "") if req else ""
    for r in rules:
        m = r.get("match") or {}
        if "model" in m and (not req or req.model != m["model"]):
            continue
        if "protocol" in m and ctx.protocol != m["protocol"]:
            continue
        if "endpoint_class" in m and ctx.endpoint_class != m["endpoint_class"]:
            continue
        if "has_tools" in m and bool(req and req.tools) != bool(m["has_tools"]):
            continue
        if "last_user_regex" in m and not re.search(m["last_user_regex"], last_user):
            continue
        if any(ctx.headers.get(k.lower()) != str(v) for k, v in (m.get("header") or {}).items()):
            continue
        return r
    return None


def rule_error(rule: dict[str, Any]) -> ErrorReply | None:
    e = rule.get("error")
    if not e:
        return None
    return ErrorReply(
        status=int(e.get("status", 400)),
        code=None if e.get("code") is None else str(e["code"]),
        message=str(e.get("message", "")),
        anthropic_type=e.get("anthropic_type", "invalid_request_error"),
        reason=f"rule:{rule.get('name')}",
    )


# ------------------------------------------------------------------ reply generation


def _example(schema: Any, key: str, text: str) -> Any:
    if not isinstance(schema, dict):
        return "example"
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for comb in ("oneOf", "anyOf"):
        if schema.get(comb) and not schema.get("properties"):
            return _example(schema[comb][0], key, text)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or "properties" in schema or "oneOf" in schema:
        return example_args(schema, text)
    if t == "array":
        return [_example(schema.get("items") or {}, key, text)]
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "null":
        return None
    m = re.search(rf"\b{re.escape(key)}\s*[=:]\s*\"?([^\s\",]+)", text) if key else None
    return m.group(1) if m else "example"


def example_args(schema: dict[str, Any], text: str = "") -> dict[str, Any]:
    """Deterministic arguments that satisfy ``schema``: required props (all props if none are required).
    A top-level oneOf/anyOf with empty ``properties`` takes its first branch (the correct behaviour that
    GLM-5#169 breaks)."""
    props = dict(schema.get("properties") or {})
    required = list(schema.get("required") or [])
    for comb in ("oneOf", "anyOf"):
        if schema.get(comb):
            branch = schema[comb][0]
            props.update(branch.get("properties") or {})
            required += [r for r in branch.get("required") or [] if r not in required]
    keys = required or list(props)
    return {k: _example(props.get(k, {}), k, text) for k in keys}


def _pick_tool(req: CanonRequest, text: str) -> Tool | None:
    if not req.tools or req.tool_choice == "none":
        return None
    if isinstance(req.tool_choice, dict) and req.tool_choice.get("name"):
        return next((t for t in req.tools if t.name == req.tool_choice["name"]), req.tools[0])
    return next((t for t in req.tools if t.name and t.name in text), req.tools[0])


def _reasoning(text: str, effort: str | None) -> str:
    snippet = re.sub(r"\s+", " ", text).strip()[:120]
    sentences = [
        f"The user asks: {snippet}.",
        "I should answer directly and keep the reply short.",
        "Checking the request once more before answering.",
    ]
    n = {"low": 1, "high": 2}.get(effort or "max", 3)
    return " ".join(sentences[:n])


def _answer(text: str) -> str:
    m = re.search(r"\b(?:say|reply(?: with)?|answer(?: with)?)\s+['\"]?([A-Za-z0-9_-]+)", text, re.I)
    if m:
        return m.group(1)
    return f"Mock GLM reply: {re.sub(r'\s+', ' ', text).strip()[:200]}"


def prompt_usage(ctx: MockContext) -> tuple[int, int]:
    """(prompt_tokens, cached_tokens). Caching is implicit and prefix-based: the longest prefix (system + tools,
    then whole earlier messages) seen in an earlier request on this server counts as cached. The newest message
    is never served from cache, so a repeated request still has uncached input (as real prefix caches do)."""
    req = ctx.req
    assert req is not None
    thinking = ctx.body.get("thinking") if isinstance(ctx.body, dict) else None
    clear = not (isinstance(thinking, dict) and thinking.get("clear_thinking") is False)  # default: reasoning ignored
    msgs = [{**m.__dict__, "reasoning": None} if clear else m.__dict__ for m in req.messages]
    head = [req.system, [t.__dict__ for t in req.tools]]
    prompt = toks(json.dumps([head, msgs], ensure_ascii=False, sort_keys=True))
    lowest = 0 if (req.system or req.tools) else 1
    cached = 0
    for k in range(len(msgs) - 1, lowest - 1, -1):
        if _sha(head, msgs[:k]) in ctx.state.prefix_seen:
            cached = min(prompt - 1, toks(json.dumps([head, msgs[:k]], ensure_ascii=False, sort_keys=True)))
            break
    for k in range(lowest, len(msgs) + 1):
        ctx.state.prefix_seen.add(_sha(head, msgs[:k]))
    return prompt, cached


def _ids(ctx: MockContext) -> str:
    return _sha(ctx.protocol, ctx.body, ctx.state.requests)[:24]


def generate(ctx: MockContext) -> Reply:
    req = ctx.req
    assert req is not None
    last = req.messages[-1]
    user_text = next((m.text for m in reversed(req.messages) if m.role == "user"), "")
    rid = _ids(ctx)
    reasoning = _reasoning(user_text, ctx.effort) if ctx.thinking else None
    content: str | None
    tool_calls: list[ToolCallOut] = []
    finish = "stop"
    tool = _pick_tool(req, user_text) if last.role == "user" else None
    if tool is not None:
        args = json.dumps(example_args(tool.parameters, user_text), ensure_ascii=False)
        tool_calls.append(ToolCallOut(id=f"call_{rid[:16]}_0", name=tool.name, arguments=args))
        content, finish = None, "tool_calls"
    elif last.role == "tool" or last.tool_results:
        result = last.tool_results[-1]["content"] if last.tool_results else ""
        content = f"Done. The tool returned: {str(result)[:120]}"
    else:
        content = _answer(user_text)
    for s in req.stop:
        if content and s in content:
            content = content[: content.index(s)]
    return finalize_reply(
        ctx,
        Reply(
            id=rid,
            model=req.model or "",
            reasoning=reasoning,
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=Usage(0, 0),
        ),
    )


def finalize_reply(ctx: MockContext, reply: Reply) -> Reply:
    """Apply max_tokens truncation and compute usage (also used for scripted replies)."""
    req = ctx.req
    assert req is not None
    budget = req.max_tokens
    r_t, c_t = toks(reply.reasoning), toks(reply.content)
    a_t = sum(toks(t.arguments) for t in reply.tool_calls)
    if budget is not None and r_t + c_t + a_t > budget:
        reply.finish_reason = "length"
        reply.tool_calls = []
        if reply.reasoning:
            reply.reasoning = reply.reasoning[: budget * 4]
        left = max(0, budget - toks(reply.reasoning))
        reply.content = (reply.content or "")[: left * 4] or None
        r_t, c_t, a_t = toks(reply.reasoning), toks(reply.content), 0
    prompt, cached = prompt_usage(ctx)
    reply.usage = Usage(
        prompt_tokens=prompt, completion_tokens=r_t + c_t + a_t, cached_tokens=cached, reasoning_tokens=r_t
    )
    if reply.reasoning:
        reply.signature = "mock-sig-" + _sha(reply.reasoning)[:16]
    return reply


def rule_reply(ctx: MockContext, rule: dict[str, Any]) -> Reply:
    spec = rule.get("reply") or {}
    req = ctx.req
    assert req is not None
    rid = _ids(ctx)
    calls = []
    for i, tc in enumerate(spec.get("tool_calls") or []):
        args = tc.get("arguments", {})
        calls.append(
            ToolCallOut(
                id=tc.get("id") or f"call_{rid[:16]}_{i}",
                name=tc["name"],
                arguments=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
            )
        )
    reasoning = spec.get("reasoning", _reasoning(req.messages[-1].text, ctx.effort) if ctx.thinking else None)
    reply = Reply(
        id=rid,
        model=spec.get("model", req.model or ""),
        reasoning=reasoning,
        content=spec.get("content"),
        tool_calls=calls,
        finish_reason=spec.get("finish_reason", "tool_calls" if calls else "stop"),
        usage=Usage(0, 0),
    )
    reply = finalize_reply(ctx, reply)
    for k, v in (spec.get("usage") or {}).items():
        setattr(reply.usage, k, v)
    return reply
