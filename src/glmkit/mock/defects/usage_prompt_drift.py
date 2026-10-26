"""USAGE-001 planted defect: prompt token counts differ from the official tokenizer/template (a swapped
tokenizer or chat template), as in zai-coding-plugins#12 where JSON counted 1.43x the estimate."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


@register_defect
class UsagePromptDrift(Defect):
    name = "usage-prompt-drift"
    title = "usage.prompt_tokens is 1.43x what the reference tokenizer counts"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/12",)
    cases = ("USAGE-001",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        reply.usage.prompt_tokens = int(reply.usage.prompt_tokens * 1.43) + 1
        return reply
