"""LANG-001/002 planted defect: CJK text appears in English-only conversations after bursts of tool calls, and a
Japanese conversation is answered in Chinese (GLM-5#142, #54)."""

from __future__ import annotations

from glmkit.agentloop.metrics import KANA, cjk_runs
from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply

ZH_ANSWER = "好的，已处理。"
LEAK = " 日志"


@register_defect
class CjkInjection(Defect):
    name = "cjk-injection"
    title = "inline CJK in English sessions after 3 tool results; Japanese sessions answered in Chinese"
    sources = ("https://github.com/zai-org/GLM-5/issues/142", "https://github.com/zai-org/GLM-5/issues/54")
    cases = ("LANG-001", "LANG-002")

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        assert ctx.req is not None
        req = ctx.req
        parts = [req.system]
        for m in req.messages:
            if m.role in ("user", "tool"):
                parts.append(m.text)
            parts += [str(r.get("content", "")) for r in m.tool_results]
        inputs = "\n".join(parts)
        if KANA.search(inputs):
            if reply.content:
                reply.content = ZH_ANSWER
            return reply
        if cjk_runs(inputs):
            return reply
        if sum(1 for m in req.messages if m.role == "tool") >= 3:
            if reply.reasoning:
                reply.reasoning += LEAK
            if reply.content:
                reply.content += LEAK
        return reply
