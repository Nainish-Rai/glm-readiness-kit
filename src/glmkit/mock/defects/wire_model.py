"""Model-name handling defects: an unknown model must be rejected, never answered by another model, and an
alias must never make the reply claim to be a model it is not."""

from __future__ import annotations

from typing import Any

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.defects._wire_common import answer_anyway
from glmkit.mock.types import ErrorReply, MockContext

PLUGINS_15 = "https://github.com/zai-org/zai-coding-plugins/issues/15"


@register_defect
class SilentModelFallback(Defect):
    name = "silent-model-fallback"
    title = "an unknown model is answered 200 by glm-5.3 instead of 400/1211 (the reply says glm-5.3)"
    sources = (PLUGINS_15,)
    cases = ("WIRE-006",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason == "unknown_model":
            return answer_anyway(ctx, model="glm-5.3") or err
        return err


@register_defect
class AliasEchoModel(Defect):
    name = "alias-echo-model"
    title = "Claude-style aliases (opus, claude-*) are answered 200 and the reply echoes the alias as its model"
    sources = ("https://docs.z.ai/devpack/tool/claude.md",)
    cases = ("ANTH-005",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        model = (ctx.req.model or "") if ctx.req else ""
        if err.reason == "unknown_model" and (model.startswith("claude-") or model in ("opus", "sonnet", "haiku")):
            return answer_anyway(ctx, model="glm-5.3", echo_model=True) or err
        return err
