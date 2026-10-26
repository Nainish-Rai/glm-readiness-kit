"""Token accounting defects: usage numbers that are zero or inflated."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, MockEvent, Reply

LARGE_BODY_BYTES = 1_000_000


@register_defect
class ZeroMessageStartUsage(Defect):
    name = "zero-message-start-usage"
    title = "Anthropic stream: message_start.usage.input_tokens is 0 (real usage only arrives in message_delta)"
    sources = ("https://github.com/zai-org/GLM-5/issues/139",)
    cases = ("ANTH-001",)

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        for e in events:
            if e.event == "message_start":
                usage = e.data["message"]["usage"]
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                    usage[k] = 0
        return events


@register_defect
class InflatedTokenCounts(Defect):
    name = "inflated-token-counts"
    title = "usage counts prompt tokens 1.4x (ASCII) to 2.8x (CJK) too high; count_tokens stays honest"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/12",)
    cases = ("ANTH-004",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        text = ctx.raw.decode("utf-8", errors="ignore")
        non_ascii = sum(1 for ch in text if ord(ch) > 127)
        factor = 2.8 if non_ascii > len(text) * 0.1 else 1.4
        reply.usage.prompt_tokens = round(reply.usage.prompt_tokens * factor)
        return reply


@register_defect
class LargeBodyZeroUsage(Defect):
    name = "large-body-zero-usage"
    title = "a request body over 1 MB is answered 200 with prompt_tokens 0 (the body was silently dropped)"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/15",)
    cases = ("WIRE-010",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if len(ctx.raw) > LARGE_BODY_BYTES:
            reply.usage.prompt_tokens = 0
            reply.usage.cached_tokens = 0
        return reply
