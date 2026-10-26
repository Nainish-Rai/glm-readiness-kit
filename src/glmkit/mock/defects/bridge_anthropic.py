"""Bridge defects on the Anthropic Messages path (issue 07). All names carry the ``bridge-anthropic-`` prefix."""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from glmkit.mock import engine
from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import ErrorReply, MockContext, MockEvent


def _is_count(ctx: MockContext) -> bool:
    return ctx.path.rstrip("/").endswith("/count_tokens")


@register_defect
class BridgeAnthropicZeroStartUsage(Defect):
    name = "bridge-anthropic-zero-start-usage"
    title = "message_start carries usage input_tokens 0 (real usage only arrives in message_delta)"
    sources = ("https://github.com/zai-org/GLM-5/issues/139",)
    cases = ("ANTH-001",)

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        if ctx.protocol != "anthropic_messages":
            return events
        for e in events:
            if e.event == "message_start":
                usage = e.data["message"]["usage"]
                usage.update(input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return events


@register_defect
class BridgeAnthropicArrayInput500(Defect):
    name = "bridge-anthropic-array-input-500"
    title = "HTTP 500 when a tool_use block in the history has an array as its input"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/14",)

    def on_request(self, ctx: MockContext) -> Any | None:
        body = ctx.body
        if ctx.protocol != "anthropic_messages" or not isinstance(body, dict):
            return None
        for m in body.get("messages") or []:
            content = m.get("content") if isinstance(m, dict) else None
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), list):
                    return JSONResponse(
                        {"type": "error", "error": {"type": "api_error", "message": "Internal server error"}},
                        status_code=500,
                    )
        return None


@register_defect
class BridgeAnthropicMissingMaxTokens500(Defect):
    name = "bridge-anthropic-missing-max-tokens-500"
    title = "HTTP 500 (instead of a clean 400) when max_tokens is missing on /v1/messages"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/15",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> ErrorReply | Any:
        if err.reason == "missing_max_tokens":
            return ErrorReply(500, None, "Internal server error", anthropic_type="api_error", reason="max_tokens_500")
        return err


@register_defect
class BridgeAnthropicInflatedCount(Defect):
    name = "bridge-anthropic-inflated-count"
    title = "count_tokens returns about 1.7x the tokens the same prompt is billed as, so the context meter lies"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/12",)
    cases = ("ANTH-004",)
    factor = 1.7

    def on_request(self, ctx: MockContext) -> Any | None:
        if not _is_count(ctx) or not isinstance(ctx.body, dict):
            return None
        ctx.req = engine.parse_request("anthropic_messages", ctx.body)
        prompt, _ = engine.prompt_usage(ctx)
        return JSONResponse({"input_tokens": round(prompt * self.factor)})
