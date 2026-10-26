"""MM-001 planted defect: a text-only model answers a request that contains an image instead of rejecting it
with 400, so the caller believes the image was understood (cc-switch#6737 family: capability not enforced)."""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import ErrorReply, MockContext


@register_defect
class TextOnlyAcceptsImage(Defect):
    name = "text-only-accepts-image"
    title = "text-only glm-5.3 returns 200 with an invented image description instead of 400/1214"
    sources = ("https://github.com/farion1231/cc-switch/issues/6737",)
    cases = ("MM-001",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason != "image_unsupported":
            return err
        body = {
            "id": "chatcmpl-mock-image",
            "object": "chat.completion",
            "created": ctx.config.created,
            "model": (ctx.body or {}).get("model"),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "The image shows a cat sitting on a sofa."},
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        return JSONResponse(body)
