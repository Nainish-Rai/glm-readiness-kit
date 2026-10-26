"""THINK-007 planted defect: identical temperature-0 requests return different text (uncalibrated FP8 KV cache
made greedy decoding non-deterministic, vllm#56868)."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


@register_defect
class NondeterministicGreedy(Defect):
    name = "nondeterministic-greedy"
    title = "temperature 0 + seed requests return a different reasoning tail each time"
    sources = ("https://github.com/vllm-project/vllm/issues/56868",)
    cases = ("THINK-007",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        st = self.state(ctx)
        st["n"] = st.get("n", 0) + 1
        tail = f" Alternatively, variant {st['n']} of the wording."
        if reply.reasoning is not None:
            reply.reasoning += tail
        elif reply.content is not None:
            reply.content += tail
        return reply
