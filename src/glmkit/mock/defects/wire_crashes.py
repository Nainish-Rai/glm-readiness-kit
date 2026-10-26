"""Hosts that answer a bad input with a 500 and a leaked Python traceback instead of a structured 4xx.

All of these are the failure family reported in zai-coding-plugins#14/#15 (and cc-switch#6737): a documented
input reaches code that does not expect it. Each defect plants one trigger so one case can catch it.
"""

from __future__ import annotations

from typing import Any

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.defects._wire_common import crash
from glmkit.mock.types import ErrorReply, MockContext

PLUGINS_15 = "https://github.com/zai-org/zai-coding-plugins/issues/15"
PLUGINS_14 = "https://github.com/zai-org/zai-coding-plugins/issues/14"


@register_defect
class AnthropicMissingMaxTokens500(Defect):
    name = "anthropic-missing-max-tokens-500"
    title = "Anthropic body without max_tokens crashes with 500 + traceback instead of a 400"
    sources = (PLUGINS_15,)
    cases = ("WIRE-008",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason == "missing_max_tokens":
            return crash("messages", "create_message", 'max_tokens = body["max_tokens"]', "KeyError: 'max_tokens'")
        return err


@register_defect
class ToolUseInputArray500(Defect):
    name = "tool-use-input-array-500"
    title = "an assistant tool_use whose input is an array crashes the converter (500 + traceback)"
    sources = (PLUGINS_14, PLUGINS_15)
    cases = ("WIRE-009",)

    def on_request(self, ctx: MockContext) -> Any:
        if ctx.protocol != "anthropic_messages" or not isinstance(ctx.body, dict):
            return None
        for m in ctx.body.get("messages") or []:
            if not isinstance(m, dict) or not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use" and not isinstance(b.get("input"), dict):
                    return crash(
                        "convert",
                        "tool_use_to_call",
                        "arguments = json.dumps(block.input.get('args', {}))",
                        "AttributeError: 'list' object has no attribute 'get'",
                        line=87,
                    )
        return None


@register_defect
class Validation500(Defect):
    name = "validation-500"
    title = "bad parameters (missing messages, temperature, top_p, max_tokens, 129 tools) crash with 500 + NoneType"
    sources = (PLUGINS_15,)
    cases = ("WIRE-007",)

    REASONS = frozenset({"missing_messages", "bad_temperature", "bad_top_p", "bad_max_tokens", "too_many_tools"})

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason in self.REASONS:
            return crash(
                "chat",
                "validate_params",
                "if request.messages[0].role == 'system':",
                "TypeError: 'NoneType' object is not subscriptable",
                line=64,
            )
        return err


@register_defect
class ContextOverflow500(Defect):
    name = "context-overflow-500"
    title = "a prompt past the context window crashes with 500 + traceback instead of 400/1261"
    sources = ("https://docs.z.ai/api-reference/api-code.md",)
    cases = ("WIRE-018",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason == "context_overflow":
            return crash(
                "chat", "tokenize", "ids = tokenizer.encode(prompt)[:limit]", "IndexError: index out of range", line=203
            )
        return err


@register_defect
class AuthMissing500(Defect):
    name = "auth-missing-500"
    title = "a request without credentials crashes with 500 + traceback instead of 401/1001"
    sources = ("https://docs.z.ai/api-reference/api-code.md",)
    cases = ("AUTH-001",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason == "auth_missing":
            return crash(
                "auth",
                "check_key",
                "scheme, token = header.split(' ')",
                "AttributeError: 'NoneType' object has no attribute 'split'",
                line=31,
            )
        return err
