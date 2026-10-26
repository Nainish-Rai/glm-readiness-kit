"""THINK-001 planted defect: the tool call is emitted as XML text inside the reasoning block and never executes
(opencode#6708)."""

from __future__ import annotations

import json

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


@register_defect
class ToolCallInReasoning(Defect):
    name = "tool-call-in-reasoning"
    title = "tool calls appear as <tool_call><arg_key> markup inside reasoning; tool_calls is empty"
    sources = ("https://github.com/anomalyco/opencode/issues/6708",)
    cases = ("THINK-001", "CACHE-002", "THINK-006")

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if not reply.tool_calls:
            return reply
        parts = []
        for t in reply.tool_calls:
            try:
                args = json.loads(t.arguments)
            except ValueError:
                args = {}
            kv = "".join(f"<arg_key>{k}</arg_key><arg_value>{v}</arg_value>" for k, v in args.items())
            parts.append(f"{t.name}{kv}</tool_call>")
        reply.reasoning = ((reply.reasoning or "") + " " + " ".join(parts)).strip()
        reply.tool_calls, reply.finish_reason = [], "stop"
        return reply
