"""CACHE-001/002, THINK-001 planted defect: the host never reports a cache hit, so every agent turn pays for the
whole history (cache.md; cost forensics in slice 12)."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


@register_defect
class NoCacheHits(Defect):
    name = "no-cache-hits"
    title = "usage.prompt_tokens_details.cached_tokens is always 0 (prefix cache never hits)"
    sources = ("https://docs.z.ai/guides/capabilities/cache",)
    cases = ("CACHE-001", "CACHE-002", "THINK-001")

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        reply.usage.cached_tokens = 0
        return reply
