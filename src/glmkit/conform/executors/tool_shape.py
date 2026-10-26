"""``tool_shape``: build the awkward request shapes of the tool cases from a few knobs, then send it once.

executor_config (all optional):
- every value may be a map ``{default: v, <variant id>: v}`` to differ per variant
- ``n_tools`` / ``target_index``: replace ``tools`` with N synthetic tools ``tool_000..``; the user asks for one.
- ``write_file_bytes``: the user asks for ``write_file`` with a tricky string of about that size (``</tool_call>``,
  ``<arg_key>``, quotes, backslashes, leading/trailing spaces, newlines, unicode).
- ``context_tokens`` (+ ``chars_per_token``, default 4): insert Claude-Code-shaped history (``read_file`` calls
  and results) before the last message until the request is about that many tokens.
- ``tool_result_bytes``: replace the placeholder ``{{PAD}}`` in tool messages by that many bytes of filler.
Large filler is cut from the *recorded* request body so result files stay small (``body_bytes`` keeps the size).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from glmkit.conform.executors import ExecContext, executor
from glmkit.contracts.evidence import Exchange

PLACEHOLDER = "{{PAD}}"
UNIT = (
    "  leading spaces are kept\n"
    "quote \" and apostrophe ' and backslash \\ and C:\\temp\\new literal \\n here\n"
    "markup </tool_call> <arg_key>k</arg_key> <arg_value>v</arg_value> </arg_value>\n"
    "tab\there; unicode caf\u00e9 \u65e5\u672c\u8a9e\n"
)
CODE_LINE = "def handler_{i}(request):\n    return {{'ok': True, 'id': {i}}}\n"


def tricky_string(size: int) -> str:
    body = (UNIT * (size // len(UNIT) + 1))[: max(0, size - 24)]
    return body + "\n</tool_call>\n  end  "


def filler_text(size: int, seed: int = 0) -> str:
    out, i = [], seed
    while sum(map(len, out)) < size:
        out.append(CODE_LINE.format(i=i))
        i += 1
    return "".join(out)[:size]


def synthetic_tools(n: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i:03d}",
                "description": f"Synthetic tool number {i}.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
        for i in range(n)
    ]


def history(tokens: int, chars_per_token: float, tool: str) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    chars, i = 0, 0
    while chars < tokens * chars_per_token:
        result = filler_text(3000, i * 40)
        call = {
            "id": f"call_hist_{i}",
            "type": "function",
            "function": {"name": tool, "arguments": json.dumps({"path": f"src/module_{i}.py"})},
        }
        msgs += [
            {"role": "user", "content": f"Open src/module_{i}.py and tell me what it does."},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": result},
            {"role": "assistant", "content": f"module_{i}.py defines small request handlers."},
        ]
        chars += 3000 + 200
        i += 1
    return msgs


FILLER_MARKS = ("call_hist_", "src/module_", "defines small request handlers")


def _shorten(msgs: Any, limit: int = 600) -> Any:
    """Compact the recorded messages: runs of generated filler history become one marker message and any other
    long string is cut."""
    out: list[Any] = []
    run = 0
    for m in msgs or []:
        if any(mark in json.dumps(m) for mark in FILLER_MARKS):
            run += 1
            continue
        if run:
            out.append({"role": "user", "content": f"[{run} generated filler history messages elided from the record]"})
            run = 0
        out.append(_cut(m, limit))
    if run:
        out.append({"role": "user", "content": f"[{run} generated filler history messages elided from the record]"})
    return out


def _cut(obj: Any, limit: int) -> Any:
    if isinstance(obj, str):
        return obj if len(obj) <= limit else f"{obj[:200]} ... [{len(obj) - 200} chars elided from the record]"
    if isinstance(obj, list):
        return [_cut(v, limit) for v in obj]
    if isinstance(obj, dict):
        return {k: _cut(v, limit) for k, v in obj.items()}
    return obj


def knob(cfg: dict[str, Any], key: str, variant: Any) -> Any:
    """A config value, or a ``{default: v, <variant id>: v}`` map resolved for the running variant."""
    val = cfg.get(key)
    if isinstance(val, dict):
        return val.get(variant.id if variant else "default", val.get("default"))
    return val


@executor("tool_shape")
async def tool_shape(ctx: ExecContext) -> list[Exchange]:
    cfg = {k: knob(ctx.config, k, ctx.variant) for k in ctx.config}
    req = copy.deepcopy(ctx.request)
    msgs: list[dict[str, Any]] = req.setdefault("messages", [])
    shrink = False
    if cfg.get("n_tools"):
        n, target = int(cfg["n_tools"]), int(cfg.get("target_index", int(cfg["n_tools"]) // 2))
        req["tools"] = synthetic_tools(n)
        ask = f"Call tool_{target:03d} with exactly these arguments: {json.dumps({'query': f'needle-{target}'})}"
        msgs.append({"role": "user", "content": ask})
    if cfg.get("write_file_bytes"):
        args = {"path": "src/generated.py", "content": tricky_string(int(cfg["write_file_bytes"]))}
        dumped = json.dumps(args, ensure_ascii=False)
        ask = f"Create the file by calling write_file with exactly these arguments: {dumped}"
        msgs.append({"role": "user", "content": ask})
    if cfg.get("tool_result_bytes"):
        pad = filler_text(int(cfg["tool_result_bytes"]))
        for m in msgs:
            if m.get("role") == "tool" and m.get("content") == PLACEHOLDER:
                m["content"] = pad
        shrink = True
    if cfg.get("context_tokens"):
        extra = history(
            int(cfg["context_tokens"]), float(cfg.get("chars_per_token", 4)), cfg.get("filler_tool", "read_file")
        )
        at = max(0, len(msgs) - 1)  # before the final user message
        msgs[at:at] = extra
        shrink = True
    ex = await ctx.client.send_canonical(req, stream=ctx.stream, label="main")
    if shrink and isinstance(ex.request.body, dict):
        ex.request.body = {**ex.request.body, "messages": _shorten(ex.request.body.get("messages"))}
    return [ex]
