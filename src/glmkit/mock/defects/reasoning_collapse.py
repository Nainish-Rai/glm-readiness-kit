"""THINK-005/006 planted defect: once the history holds N assistant turns with replayed reasoning, the model's
reasoning collapses into ``= the = the`` until max_tokens and the answer is empty (GLM-5#168, vllm#56868)."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.engine import toks
from glmkit.mock.types import MockContext, Reply


@register_defect
class ReasoningCollapse(Defect):
    name = "reasoning-collapse"
    title = "reasoning collapses into '= the' loops after 4 replayed assistant turns; empty content, finish=length"
    sources = (
        "https://github.com/zai-org/GLM-5/issues/168",
        "https://github.com/vllm-project/vllm/issues/56868",
    )
    cases = ("THINK-005", "THINK-006")
    after_turns = 4

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        assert ctx.req is not None
        replayed = sum(1 for m in ctx.req.messages if m.role == "assistant" and m.reasoning)
        if replayed < self.after_turns:
            return reply
        reply.reasoning = " ".join(["=", "the"] * 60)
        reply.content, reply.tool_calls, reply.finish_reason = None, [], "length"
        reply.usage.completion_tokens = reply.usage.reasoning_tokens = toks(reply.reasoning)
        return reply
