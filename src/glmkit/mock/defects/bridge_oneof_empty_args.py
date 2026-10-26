"""Bridge defect: a tool whose parameters are a top-level oneOf with no ``properties`` comes back with ``{}``
arguments (zai-org/GLM-5#169). The bridge flattens such schemas before they reach the model."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


@register_defect
class BridgeOneOfEmptyArgs(Defect):
    name = "bridge-oneof-empty-args"
    title = "top-level oneOf tool schema with empty properties yields {} arguments"
    sources = ("https://github.com/zai-org/GLM-5/issues/169",)
    cases = ()

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        assert ctx.req is not None
        tools = {t.name: t for t in ctx.req.tools}
        for tc in reply.tool_calls:
            p = tools[tc.name].parameters if tc.name in tools else {}
            if (p.get("oneOf") or p.get("anyOf")) and not p.get("properties"):
                tc.arguments = "{}"
        return reply
