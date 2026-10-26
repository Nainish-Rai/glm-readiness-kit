"""FP-002..FP-005's planted defect: a host serving a lower-precision model. Tool choice and text drift
(random but seeded by the request and how often it was repeated, so a run is reproducible), identical
temperature-0 requests stop agreeing, and the capability canaries lose accuracy."""

from __future__ import annotations

import hashlib
import json
import random
from typing import ClassVar

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.engine import example_args
from glmkit.mock.types import MockContext, Reply


def perturb(text: str, rng: random.Random, share: float = 0.25) -> str:
    """Replace about ``share`` of the characters (at least one) so the text certainly changes."""
    chars = list(text)
    if not chars:
        return text
    idx = [i for i, c in enumerate(chars) if not c.isspace()] or list(range(len(chars)))
    picks = rng.sample(idx, max(1, round(len(idx) * share)))
    for i in picks:
        c = chars[i]
        pool = "0123456789" if c.isdigit() else "abcdefghijklmnopqrstuvwxyz"
        chars[i] = rng.choice([p for p in pool if p != c.lower()])
    return "".join(chars)


@register_defect
class Quantized(Defect):
    name = "quantized"
    title = "quantized host: tool choice and text drift, greedy runs disagree, canary accuracy drops"
    sources = ("https://arxiv.org/abs/2609.00052", "https://github.com/vllm-project/vllm/issues/56868")
    cases = ("FP-002", "FP-003", "FP-004", "FP-005")
    p_tool: ClassVar[float] = 0.3  # share of tool calls replaced by another tool
    p_text: ClassVar[float] = 0.3  # share of text replies perturbed

    def _rng(self, ctx: MockContext) -> random.Random:
        """Seeded by the request body and the number of times this exact body was seen: reproducible, and the
        same request repeated gets fresh draws (so greedy self-agreement drops)."""
        key = hashlib.sha256(ctx.raw).hexdigest()
        seen = self.state(ctx)
        n = seen.get(key, 0)
        seen[key] = n + 1
        return random.Random(f"quantized:{key}:{n}")

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        rng = self._rng(ctx)
        tools = ctx.req.tools if ctx.req else []
        if reply.tool_calls and len(tools) > 1:
            for tc in reply.tool_calls:
                if rng.random() < self.p_tool:
                    other = rng.choice([t for t in tools if t.name != tc.name])
                    tc.name = other.name
                    tc.arguments = json.dumps(example_args(other.parameters))
        elif reply.content and not reply.tool_calls and rng.random() < self.p_text:
            reply.content = perturb(reply.content, rng)
        return reply
