"""WIRE-001's planted defect: a host that crashes instead of returning 400/1210 when thinking is disabled on
a forced-thinking model, leaking a Python traceback (the same failure family as zai-coding-plugins#15)."""

from __future__ import annotations

from starlette.responses import PlainTextResponse

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import ErrorReply, MockContext

TRACEBACK = """Traceback (most recent call last):
  File "/srv/gateway/handlers/chat.py", line 212, in create_completion
    effort = THINKING_EFFORT[req.thinking.type]
             ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
KeyError: 'disabled'
"""


@register_defect
class ForcedThinking500(Defect):
    name = "forced-thinking-500"
    title = "thinking disabled on a forced-thinking model returns 500 with a Python traceback instead of 400/1210"
    sources = (
        "https://github.com/farion1231/cc-switch/issues/6737",
        "https://github.com/zai-org/zai-coding-plugins/issues/15",
    )
    cases = ("WIRE-001",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> ErrorReply | PlainTextResponse:
        if err.reason == "forced_thinking":
            return PlainTextResponse(TRACEBACK, status_code=500)
        return err
