"""STRM-003 planted defect: ``tool_stream: true`` on a non-streaming request crashes the gateway with a 500
and a leaked stack trace instead of a 400 (or being ignored)."""

from __future__ import annotations

from starlette.responses import PlainTextResponse, Response

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext

TRACEBACK = """Traceback (most recent call last):
  File "/srv/gateway/handlers/chat.py", line 97, in create_completion
    return stream_tools(req.messages)
AttributeError: 'NoneType' object has no attribute 'iter_tool_deltas'
"""


@register_defect
class ToolStreamWithoutStream500(Defect):
    name = "tool-stream-without-stream-500"
    title = "tool_stream:true without stream:true returns 500 with a Python traceback"
    sources = ("https://docs.z.ai/guides/capabilities/stream-tool",)
    cases = ("STRM-003",)

    def on_request(self, ctx: MockContext) -> Response | None:
        body = ctx.body if isinstance(ctx.body, dict) else {}
        if body.get("tool_stream") and not body.get("stream"):
            return PlainTextResponse(TRACEBACK, status_code=500)
        return None
