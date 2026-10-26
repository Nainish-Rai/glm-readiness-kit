"""FP-001's planted defect: the host answers with another model and reports it (or answers an unknown model with
a silent fallback, zai-coding-plugins#15 bug 4)."""

from __future__ import annotations

from typing import ClassVar

from starlette.responses import JSONResponse

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import ErrorReply, MockContext, Reply


@register_defect
class WrongModelField(Defect):
    name = "wrong-model-field"
    title = "response model differs from the request; unknown models are answered by a fallback model"
    sources = ("https://github.com/zai-org/zai-coding-plugins/issues/15",)
    cases = ("FP-001",)
    served_model: ClassVar[str] = "glm-4.7"

    def on_error(self, ctx: MockContext, err: ErrorReply) -> ErrorReply | JSONResponse:
        if err.reason == "unknown_model" and ctx.protocol == "openai_chat":
            body = {
                "id": "mock-fallback",
                "object": "chat.completion",
                "created": ctx.config.created,
                "model": self.served_model,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
            return JSONResponse(body)
        return err

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        reply.model = self.served_model
        return reply
