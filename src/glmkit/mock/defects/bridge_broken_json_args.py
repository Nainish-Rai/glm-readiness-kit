"""Bridge defect: tool-call arguments that are not a clean JSON object. Four styles rotate per tool call
(single quotes + trailing comma + Python literal, a JSON string holding JSON, a truncated object, a code fence);
all are repairable without guessing."""

from __future__ import annotations

import json

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


def _break(args: str, style: int) -> str:
    obj = json.loads(args)
    if style == 0:
        s = json.dumps(obj).replace('"', "'").replace("true", "True").replace("false", "False")
        return s[:-1] + ",}"
    if style == 1:
        return json.dumps(args)  # the whole object as a JSON string
    if style == 2:
        return args[: max(2, len(args) - 1)]  # closing brace lost
    return "```json\n" + args + "\n```"


@register_defect
class BridgeBrokenJsonArgs(Defect):
    name = "bridge-broken-json-args"
    title = "tool-call arguments are single-quoted / double-encoded / truncated / fenced instead of a JSON object"
    sources = ("https://github.com/musistudio/claude-code-router (enhancetool)", "research/02-bridge.md 6.2")
    cases = ()

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        st = self.state(ctx)
        for tc in reply.tool_calls:
            try:
                tc.arguments = _break(tc.arguments, st.get("n", 0) % 4)
            except ValueError:
                continue
            st["n"] = st.get("n", 0) + 1
        return reply
