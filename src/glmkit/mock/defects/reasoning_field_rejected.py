"""THINK-001/003 planted defect: a host that rejects ``reasoning_content`` on replayed assistant messages with a
400, so a harness that follows the Z.ai docs (replay reasoning) cannot continue its loop (opencode#26762)."""

from __future__ import annotations

from starlette.responses import JSONResponse, Response

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext


@register_defect
class ReasoningFieldRejected(Defect):
    name = "reasoning-field-rejected"
    title = "assistant history carrying reasoning_content is rejected with 400 (property unsupported)"
    sources = ("https://github.com/anomalyco/opencode/issues/26762",)
    cases = ("THINK-001", "THINK-003")

    def on_request(self, ctx: MockContext) -> Response | None:
        body = ctx.body if isinstance(ctx.body, dict) else {}
        for i, m in enumerate(body.get("messages") or []):
            if isinstance(m, dict) and m.get("role") == "assistant" and "reasoning_content" in m:
                msg = f"messages.{i}.assistant.reasoning_content: property 'reasoning_content' is unsupported"
                return JSONResponse(
                    {"error": {"message": msg, "type": "invalid_request_error", "code": "wrong_api_format"}},
                    status_code=400,
                )
        return None
