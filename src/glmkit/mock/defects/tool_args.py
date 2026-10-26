"""Planted defects that corrupt the arguments of a tool call (TOOL-004, 005, 006, 014)."""

from __future__ import annotations

import json
import re
from typing import Any

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


def _args(arguments: str) -> dict[str, Any] | None:
    try:
        v = json.loads(arguments)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


@register_defect
class OneOfEmptyArgs(Defect):
    name = "oneof-empty-args"
    title = "top-level oneOf with empty properties yields {} arguments (GLM-5#169)"
    sources = ("https://github.com/zai-org/GLM-5/issues/169",)
    cases = ("TOOL-006",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        assert ctx.req is not None
        tools = {t.name: t for t in ctx.req.tools}
        for tc in reply.tool_calls:
            p = tools[tc.name].parameters if tc.name in tools else {}
            if p.get("oneOf") and not p.get("properties"):
                tc.arguments = "{}"
        return reply


@register_defect
class TypedArgsStringified(Defect):
    name = "typed-args-stringified"
    title = "int / float / bool / null arguments come back as strings ('1', 'None') (sglang#46037, #35564)"
    sources = (
        "https://github.com/sgl-project/sglang/issues/46037",
        "https://github.com/sgl-project/sglang/issues/35564",
    )
    cases = ("TOOL-004",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        for tc in reply.tool_calls:
            args = _args(tc.arguments)
            if args is not None:
                tc.arguments = json.dumps(
                    {k: (v if isinstance(v, str | list | dict) else str(v)) for k, v in args.items()},
                    ensure_ascii=False,
                )
        return reply


@register_defect
class ToolCallEndTruncation(Defect):
    name = "tool-call-end-truncation"
    title = "a literal </tool_call> inside an argument ends the call early; the rest leaks into content (ollama#18659)"
    sources = ("https://github.com/ollama/ollama/issues/18659", "https://github.com/charmbracelet/crush/issues/3153")
    cases = ("TOOL-005",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        for tc in reply.tool_calls:
            i = tc.arguments.find("</tool_call>")
            if i >= 0:
                reply.content = (reply.content or "") + tc.arguments[i:]
                tc.arguments = tc.arguments[:i]
        return reply


@register_defect
class CommandPrefixDropped(Defect):
    name = "command-prefix-dropped"
    title = "the model plans `cd X && cmd` but emits `cmd` (command prefix lost) (GLM-5#116)"
    sources = ("https://github.com/zai-org/GLM-5/issues/116",)
    cases = ("TOOL-014",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        for tc in reply.tool_calls:
            args = _args(tc.arguments)
            if args and isinstance(args.get("command"), str):
                args["command"] = re.sub(r"^\s*cd\s+\S+\s*&&\s*", "", args["command"])
                tc.arguments = json.dumps(args, ensure_ascii=False)
        return reply
