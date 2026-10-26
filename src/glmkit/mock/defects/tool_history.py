"""Planted defects on tool-call history replay (TOOL-009, 010)."""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse, PlainTextResponse

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext

JINJA_ERROR = """Traceback (most recent call last):
  File "/srv/engine/chat_template.py", line 88, in render
    for k, v in tool_call.arguments|items
jinja2.exceptions.TypeError: Can only get item pairs from a mapping.
"""


def _messages(ctx: MockContext) -> list[dict[str, Any]]:
    body = ctx.body if isinstance(ctx.body, dict) else {}
    return [m for m in body.get("messages") or [] if isinstance(m, dict)]


@register_defect
class DictOnlyChatTemplate(Defect):
    name = "dict-only-chat-template"
    title = "chat template iterates tool_calls[].function.arguments as a mapping, so OpenAI string arguments crash it"
    sources = ("https://github.com/zai-org/GLM-5/issues/158",)
    cases = ("TOOL-009",)

    def on_request(self, ctx: MockContext) -> Any | None:
        for m in _messages(ctx):
            for tc in m.get("tool_calls") or []:
                if isinstance((tc.get("function") or {}).get("arguments"), str):
                    return PlainTextResponse(JINJA_ERROR, status_code=500)
        return None


@register_defect
class NullContentRejected(Defect):
    name = "null-content-rejected"
    title = "assistant turn with content null (standard for tool-call turns) is rejected with a 400"
    sources = ("https://github.com/vllm-project/vllm/issues/56868", "https://github.com/zai-org/GLM-5/issues/158")
    cases = ("TOOL-009",)

    def on_request(self, ctx: MockContext) -> Any | None:
        for i, m in enumerate(_messages(ctx)):
            if m.get("role") == "assistant" and m.get("tool_calls") and m.get("content") is None:
                msg = f"messages[{i}].content: expected string, got null"
                return JSONResponse({"error": {"code": "1214", "message": msg}}, status_code=400)
        return None


@register_defect
class EmptyToolResultCrash(Defect):
    name = "empty-tool-result-crash"
    title = "an empty tool result string crashes the host with a 500"
    sources = ("https://docs.z.ai/guides/capabilities/function-calling.md",)
    cases = ("TOOL-010",)

    def on_request(self, ctx: MockContext) -> Any | None:
        for m in _messages(ctx):
            if m.get("role") == "tool" and m.get("content") in ("", None):
                return PlainTextResponse(
                    'Traceback (most recent call last):\n  File "/srv/gateway/tools.py", line 41, in fold\n'
                    "    last = result[-1]\nIndexError: string index out of range\n",
                    status_code=500,
                )
        return None
