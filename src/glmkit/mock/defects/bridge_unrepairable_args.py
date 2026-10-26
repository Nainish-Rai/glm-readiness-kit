"""Bridge defect: tool-call arguments that are prose, not JSON. They cannot be repaired without inventing data,
so the right behaviour is one retry with the parse error injected. The mock answers correctly once the request
carries that injected error (a tool message starting with the bridge's marker)."""

from __future__ import annotations

import json

from glmkit.mock import engine
from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply, ToolCallOut

MARKER = "Tool call arguments were not valid"


@register_defect
class BridgeUnrepairableArgs(Defect):
    name = "bridge-unrepairable-args"
    title = "tool-call arguments are prose; correct only after the parse error is shown to the model"
    sources = ("research/02-bridge.md 3.4 (retry-with-parse-error)",)
    cases = ()

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        assert ctx.req is not None
        req = ctx.req
        retried = any(MARKER in r.get("content", "") for m in req.messages for r in m.tool_results)
        if retried:
            failed = [c for m in req.messages for c in m.tool_calls]
            tool = next((t for t in req.tools if failed and t.name == failed[-1].get("name")), None)
            if tool:
                user = next((m.text for m in reversed(req.messages) if m.role == "user"), "")
                args = json.dumps(engine.example_args(tool.parameters, user))
                reply.tool_calls = [ToolCallOut(id=failed[-1]["id"] or "call_retry", name=tool.name, arguments=args)]
                reply.content, reply.finish_reason = None, "tool_calls"
            return reply
        for tc in reply.tool_calls:
            tc.arguments = f"Action: call {tc.name} with the arguments from the user message"
        return reply
