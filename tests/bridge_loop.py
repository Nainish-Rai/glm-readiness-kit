"""A small tool-calling loop written with the OpenAI Python SDK (dev dependency only). Used by the bridge tests and by
``tools/bridge-proof/proof.py``: it runs the same loop direct and through the bridge and reports what happened."""

from __future__ import annotations

import json
from typing import Any

import openai

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}, "unit": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look something up by query or by url",
            "parameters": {
                "oneOf": [
                    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                ]
            },
        },
    },
]


def run_tool(name: str, args: Any) -> str:
    if not isinstance(args, dict):
        raise ValueError(f"{name}: arguments are {type(args).__name__}, not an object")
    if name == "get_weather":
        return f"sunny in {args['city']}"
    if name == "lookup":
        if "query" not in args and "url" not in args:
            raise ValueError("lookup: needs query or url, got " + json.dumps(args))
        return "found " + str(args.get("query") or args.get("url"))
    raise ValueError("unknown tool " + name)


def _collect_stream(stream: Any) -> dict[str, Any]:
    """Client-side accumulation of a streamed assistant message (what any SDK app does)."""
    msg: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": {}}
    reasoning, finish = "", None
    for chunk in stream:
        if not chunk.choices:
            continue
        ch = chunk.choices[0]
        d = ch.delta
        if d.content:
            msg["content"] += d.content
        extra = d.model_extra or {}
        reasoning += extra.get("reasoning_content") or ""
        for tc in d.tool_calls or []:
            slot = msg["tool_calls"].setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
            slot["id"] = tc.id or slot["id"]
            if tc.function:
                slot["name"] += tc.function.name or ""
                slot["arguments"] += tc.function.arguments or ""
        finish = ch.finish_reason or finish
    calls = [
        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
        for _, c in sorted(msg["tool_calls"].items())
    ]
    out: dict[str, Any] = {"role": "assistant", "content": msg["content"] or None}
    if calls:
        out["tool_calls"] = calls
    if reasoning:
        out["reasoning_content"] = reasoning
    out["_finish"] = finish
    return out


def run_loop(
    base_url: str,
    *,
    stream: bool = False,
    prompts: tuple[str, ...] = (
        "Use the get_weather tool for city=Paris please",
        "Now use the lookup tool with query=glm please",
    ),
    thinking_disabled: bool = True,
    effort: str | None = "medium",
    headers: dict[str, str] | None = None,
    model: str = "glm-5.3",
    max_turns: int = 6,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    client = openai.OpenAI(base_url=base_url, api_key="sk-test-not-a-real-key", max_retries=0, default_headers=headers)
    extra: dict[str, Any] = {"tool_stream": True} if stream else {}
    if thinking_disabled:
        extra["thinking"] = {"type": "disabled"}
    kw: dict[str, Any] = {"extra_body": extra}
    if effort:
        kw["reasoning_effort"] = effort
    messages: list[dict[str, Any]] = []
    results: list[str] = []
    final = None
    try:
        for prompt in prompts:
            messages.append({"role": "user", "content": prompt})
            for _ in range(max_turns):
                if stream:
                    msg = _collect_stream(
                        client.chat.completions.create(
                            model=model, messages=messages, tools=tools or TOOLS, stream=True, **kw
                        )
                    )
                else:
                    resp = client.chat.completions.create(model=model, messages=messages, tools=tools or TOOLS, **kw)
                    m = resp.choices[0].message
                    msg = {"role": "assistant", "content": m.content}
                    if m.tool_calls:
                        msg["tool_calls"] = [tc.model_dump() for tc in m.tool_calls]
                    if (m.model_extra or {}).get("reasoning_content"):
                        msg["reasoning_content"] = m.model_extra["reasoning_content"]
                msg.pop("_finish", None)
                messages.append(msg)
                if not msg.get("tool_calls"):
                    final = msg["content"]
                    break
                for tc in msg["tool_calls"]:
                    args = json.loads(tc["function"]["arguments"])
                    out = run_tool(tc["function"]["name"], args)
                    results.append(out)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
            else:
                return {"ok": False, "error": "max turns", "tool_results": results}
        return {"ok": True, "final": final, "tool_results": results, "messages": len(messages)}
    except openai.APIStatusError as e:
        return {"ok": False, "error": f"HTTP {e.status_code}: {str(e.message)[:120]}", "tool_results": results}
    except (ValueError, KeyError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}", "tool_results": results}
