"""Auth, thinking-history and rate-limit defects."""

from __future__ import annotations

import time
from typing import Any

from starlette.responses import JSONResponse, PlainTextResponse

from glmkit.mock import encode
from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import ErrorReply, MockContext

BURST_WINDOW_S = 10.0
BURST_FREE = 4  # requests inside the window that are still served


@register_defect
class AnthropicBearerRejected(Defect):
    name = "anthropic-bearer-rejected"
    title = "the Anthropic path rejects Authorization: Bearer (only x-api-key works), as ANTH-002 documents both"
    sources = ("https://docs.z.ai/devpack/tool/claude.md",)
    cases = ("ANTH-002",)

    def on_request(self, ctx: MockContext) -> Any:
        if ctx.protocol == "anthropic_messages" and "authorization" in ctx.headers and "x-api-key" not in ctx.headers:
            err = ErrorReply(401, "1001", "Authentication parameter not received in Header", "authentication_error")
            return JSONResponse(encode.error_body(ctx, err), status_code=401)
        return None


@register_defect
class ThinkingBlockRejected(Defect):
    name = "thinking-block-rejected"
    title = "a history turn that carries the assistant's thinking block (with signature) is rejected with 400"
    sources = ("https://docs.z.ai/devpack/latest-model.md",)
    cases = ("ANTH-003",)

    def on_request(self, ctx: MockContext) -> Any:
        if ctx.protocol != "anthropic_messages" or not isinstance(ctx.body, dict):
            return None
        for m in ctx.body.get("messages") or []:
            blocks = m.get("content") if isinstance(m, dict) else None
            if isinstance(blocks, list) and any(isinstance(b, dict) and b.get("type") == "thinking" for b in blocks):
                err = ErrorReply(400, "1210", "Invalid API parameter: thinking block in history", "bad_history")
                return JSONResponse(encode.error_body(ctx, err), status_code=400)
        return None


class _Burst(Defect):
    """More than BURST_FREE requests inside BURST_WINDOW_S are throttled (shared by the two variants)."""

    def throttled(self, ctx: MockContext) -> bool:
        now = time.monotonic()
        seen = [t for t in self.state(ctx).get("t", []) if now - t < BURST_WINDOW_S]
        seen.append(now)
        self.state(ctx)["t"] = seen
        return len(seen) > BURST_FREE


@register_defect
class RateLimitBurst(_Burst):
    name = "rate-limit-burst"
    title = "a burst is throttled the documented way: 429, code 1302, Retry-After header"
    sources = ("https://docs.z.ai/api-reference/api-code.md",)
    cases = ("LIM-001",)

    def on_request(self, ctx: MockContext) -> Any:
        if self.throttled(ctx):
            err = ErrorReply(429, "1302", "Rate limit reached for requests. Retry after 2 seconds.", "rate_limit_error")
            return JSONResponse(encode.error_body(ctx, err), status_code=429, headers={"retry-after": "2"})
        return None


@register_defect
class RateLimitBare(_Burst):
    name = "rate-limit-bare"
    title = "a burst is throttled with a bare 429 'Too Many Requests': no code, no Retry-After, not JSON"
    sources = ("https://docs.z.ai/api-reference/api-code.md",)
    cases = ("LIM-001",)

    def on_request(self, ctx: MockContext) -> Any:
        if self.throttled(ctx):
            return PlainTextResponse("Too Many Requests", status_code=429)
        return None
