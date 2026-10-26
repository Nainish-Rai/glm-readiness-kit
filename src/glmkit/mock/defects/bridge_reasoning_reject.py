"""Bridge defect: a host (Cerebras) that rejects ``reasoning_content`` in request messages with a 4xx. The bridge's
``cerebras`` upstream profile strips it."""

from __future__ import annotations

from starlette.responses import JSONResponse

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext


@register_defect
class BridgeReasoningReject(Defect):
    name = "bridge-reasoning-reject"
    title = "assistant message with reasoning_content is rejected (Cerebras: unrecognized request argument)"
    sources = ("research/02-bridge.md 6.3",)
    cases = ()

    def on_request(self, ctx: MockContext) -> JSONResponse | None:
        body = ctx.body if isinstance(ctx.body, dict) else {}
        for m in body.get("messages") or []:
            if isinstance(m, dict) and m.get("role") == "assistant" and ("reasoning_content" in m or "reasoning" in m):
                return JSONResponse(
                    {
                        "error": {
                            "message": "Unrecognized request argument supplied: reasoning_content",
                            "type": "invalid_request_error",
                            "param": "messages",
                            "code": "wrong_api_format",
                        }
                    },
                    status_code=422,
                )
        return None
