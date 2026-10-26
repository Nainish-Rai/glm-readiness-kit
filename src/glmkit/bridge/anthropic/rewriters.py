"""Anthropic-level request rewriters. Same contract as ``glmkit.bridge.core.pipeline``: ``(ctx, body) -> body``,
run on a private copy, no I/O. ``ctx.extras`` carries per-call settings the handler put there:
``default_max_tokens`` and ``search_backend``.

``COMMON`` runs for both upstream kinds. ``NATIVE`` (thinking and effort) runs only when the upstream speaks the
Anthropic protocol itself; for an OpenAI-compatible upstream the core's own rewriters do those fixes on the
translated request.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

from glmkit.bridge.anthropic import websearch as ws
from glmkit.bridge.core.jsonrepair import ArgumentsUnrepairable, repair_arguments
from glmkit.bridge.core.pipeline import RewriteContext
from glmkit.bridge.core.upstream import DISABLED_VALUES

Body = dict[str, Any]
Rewriter = Callable[[RewriteContext, Body], Body]
DEFAULT_MAX_TOKENS = 8192
SERVER_BLOCKS = ("server_tool_use", "web_search_tool_result")


# ---------------------------------------------------------------------------------------- max_tokens


def rewrite_max_tokens(ctx: RewriteContext, body: Body) -> Body:
    """``max_tokens`` is required by the Messages API; some hosts answer 500 instead of 400 when it is missing."""
    if body.get("max_tokens") is not None:
        return body
    value = int(ctx.extras.get("default_max_tokens") or DEFAULT_MAX_TOKENS)
    body["max_tokens"] = value
    ctx.emit("anthropic-max-tokens-default", "max_tokens missing", f"max_tokens={value}", details={"max_tokens": value})
    return body


# ---------------------------------------------------------------------------------------- web_search


def rewrite_web_search(ctx: RewriteContext, body: Body) -> Body:
    tools = body.get("tools")
    if not isinstance(tools, list) or not any(ws.is_web_search_tool(t) for t in tools):
        return body
    spec = next(t for t in tools if ws.is_web_search_tool(t))
    forced = isinstance(body.get("tool_choice"), dict) and body["tool_choice"].get("name") == ws.WEB_SEARCH_NAME
    if ctx.extras.get("search_backend") is not None:
        max_uses = int(spec.get("max_uses") or ws.DEFAULT_MAX_USES)
        body["tools"] = [ws.function_tool() if ws.is_web_search_tool(t) else t for t in tools]
        ctx.extras["web_search"] = {"max_uses": max_uses}
        ctx.emit(
            "anthropic-web-search-emulate",
            "server tool web_search (not supported by the upstream)",
            f"function tool web_search, searched by the bridge (max_uses={max_uses})",
            details={"max_uses": max_uses},
        )
        return body
    kept = [t for t in tools if not ws.is_web_search_tool(t)]
    if kept:
        body["tools"] = kept
    else:
        body.pop("tools", None)
        body.pop("tool_choice", None)
    if forced or not kept:
        body.pop("tool_choice", None)
    ctx.emit(
        "anthropic-web-search-strip",
        "server tool web_search, no search backend configured",
        "tool removed" + ("; forced tool_choice dropped" if forced else ""),
        details={"tools_left": len(kept)},
    )
    return body


# ---------------------------------------------------------------------------------------- history


def _results_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, dict):
        return f"web_search error: {content.get('error_code', 'unknown')}"
    rows = [
        f"{i}. {r.get('title', '')}\n{r.get('url', '')}" for i, r in enumerate(content or [], 1) if isinstance(r, dict)
    ]
    return "\n\n".join(rows) or "No results."


def _history_server_blocks(ctx: RewriteContext, msgs: list[Any], emulate: bool) -> tuple[list[Any], int]:
    out: list[Any] = []
    changed = 0
    for m in msgs:
        c = m.get("content") if isinstance(m, dict) else None
        if not (isinstance(m, dict) and m.get("role") == "assistant" and isinstance(c, list)):
            out.append(m)
            continue
        if not any(isinstance(b, dict) and b.get("type") in SERVER_BLOCKS for b in c):
            out.append(m)
            continue
        changed += 1
        cur: list[Any] = []
        for b in c:
            t = b.get("type") if isinstance(b, dict) else None
            if t == "server_tool_use":
                if emulate:
                    cur.append(
                        {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": b.get("input") or {}}
                    )
            elif t == "web_search_tool_result":
                if emulate:
                    if cur:
                        out.append({"role": "assistant", "content": cur})
                    out.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": b.get("tool_use_id"),
                                    "content": _results_text(b),
                                }
                            ],
                        }
                    )
                    cur = []
            else:
                cur.append(b)
        if cur:
            out.append({"role": "assistant", "content": cur})
    return out, changed


def _fix_orphans(msgs: list[Any]) -> tuple[list[Any], int]:
    seen: set[str] = set()
    fixed = 0
    for m in msgs:
        if not isinstance(m, dict) or not isinstance(m.get("content"), list):
            continue
        if m.get("role") == "assistant":
            seen.update(
                b["id"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
            )
            continue
        blocks = []
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") not in seen:
                text = b.get("content")
                text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
                blocks.append(
                    {"type": "text", "text": f"[tool result for an unknown call {b.get('tool_use_id')}] {text}"}
                )
                fixed += 1
            else:
                blocks.append(b)
        m["content"] = blocks
    return msgs, fixed


def rewrite_history(ctx: RewriteContext, body: Body) -> Body:
    """Server-tool blocks in earlier turns (converted when emulating, dropped otherwise) and orphan tool_results."""
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body
    msgs, server = _history_server_blocks(ctx, msgs, bool(ctx.extras.get("web_search")))
    msgs, orphans = _fix_orphans(msgs)
    body["messages"] = msgs
    if server or orphans:
        ctx.emit(
            "anthropic-history-repair",
            f"{server} turn(s) with server-tool blocks, {orphans} orphan tool_result block(s)",
            "server-tool blocks converted or removed; orphan results turned into text",
            details={"server_tool_turns": server, "orphan_tool_results": orphans},
        )
    return body


# ---------------------------------------------------------------------------------------- tool_use input


def repair_array_input(arr: list[Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """An array where an object belongs. One dict -> that dict; several dicts -> merged; [key, value] pairs ->
    object; a schema with exactly one array property -> wrapped under it; otherwise kept losslessly under ``_input``."""
    if arr and all(isinstance(x, dict) for x in arr):
        merged: dict[str, Any] = {}
        for x in arr:
            merged.update(x)
        return merged
    if arr and all(isinstance(x, list | tuple) and len(x) == 2 and isinstance(x[0], str) for x in arr):
        return {k: v for k, v in arr}
    props = (schema or {}).get("properties") or {}
    arrays = [k for k, v in props.items() if isinstance(v, dict) and v.get("type") == "array"]
    if len(arrays) == 1:
        return {arrays[0]: arr}
    return {"_input": arr}


def rewrite_tool_inputs(ctx: RewriteContext, body: Body) -> Body:
    """Record each tool's schema and make every ``tool_use.input`` in the history a JSON object."""
    for t in body.get("tools") or []:
        if isinstance(t, dict) and t.get("name") and isinstance(t.get("input_schema"), dict):
            ctx.tool_schemas[t["name"]] = copy.deepcopy(t["input_schema"])
    repaired: list[str] = []
    for m in body.get("messages") or []:
        content = m.get("content") if isinstance(m, dict) else None
        for b in content if isinstance(content, list) else []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use") or isinstance(b.get("input"), dict):
                continue
            raw = b.get("input")
            name = b.get("name", "")
            if isinstance(raw, list):
                b["input"] = repair_array_input(raw, ctx.tool_schemas.get(name))
                repaired.append(f"{name}:array")
            elif raw is None:
                b["input"] = {}
                repaired.append(f"{name}:null")
            else:
                try:
                    obj, _ = repair_arguments(raw)
                    b["input"] = obj
                except ArgumentsUnrepairable:
                    b["input"] = {"_input": raw}
                repaired.append(f"{name}:{type(raw).__name__}")
    if repaired:
        ctx.emit(
            "anthropic-tool-input-repair",
            f"{len(repaired)} tool_use input(s) that are not a JSON object",
            "inputs rewritten as objects before the request reaches the upstream",
            details={"inputs": repaired},
        )
    return body


# ---------------------------------------------------------------------------------------- native upstream only


def _disabled(thinking: Any) -> bool:
    if isinstance(thinking, dict):
        t = thinking.get("type")
        return t is False or str(t).lower() in DISABLED_VALUES
    return thinking is False or (isinstance(thinking, str) and thinking.lower() in DISABLED_VALUES)


def rewrite_thinking_native(ctx: RewriteContext, body: Body) -> Body:
    """``thinking: disabled`` on a forced-thinking model: enabled + ``output_config.effort: low`` (Claude Code's
    helper requests, such as titles and summaries, send disabled)."""
    if "thinking" not in body or not ctx.upstream.forces_thinking(body.get("model")) or not _disabled(body["thinking"]):
        return body
    before = json.dumps(body["thinking"])
    body["thinking"] = {"type": "enabled"}
    cfg = dict(body.get("output_config") or {})
    prior = cfg.get("effort")
    if prior is None or str(prior).lower() in DISABLED_VALUES | {"minimal"}:
        cfg["effort"] = "low"
    body["output_config"] = cfg
    ctx.emit(
        "thinking-disabled-to-low",
        f"thinking={before}",
        f"thinking={json.dumps(body['thinking'])}, output_config.effort={cfg['effort']}",
        details={"model": body.get("model")},
    )
    return body


def rewrite_effort_native(ctx: RewriteContext, body: Body) -> Body:
    cfg = body.get("output_config")
    up = ctx.upstream
    if not isinstance(cfg, dict) or "effort" not in cfg or not up.clamps_effort(body.get("model")):
        return body
    raw = cfg["effort"]
    val = str(raw).lower()
    if val in up.effort_allowed:
        cfg["effort"] = val
        return body
    new = up.effort_map.get(val, up.effort_default)
    cfg["effort"] = new
    ctx.emit("effort-clamp", f"effort={raw!r}", f"effort={new!r}", details={"model": body.get("model")})
    return body


COMMON: tuple[Rewriter, ...] = (rewrite_web_search, rewrite_history, rewrite_tool_inputs, rewrite_max_tokens)
NATIVE: tuple[Rewriter, ...] = (rewrite_thinking_native, rewrite_effort_native)
