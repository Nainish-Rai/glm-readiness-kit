"""Request-parameter defects: effort, tool_choice, stop words, JSON mode and do_sample not honoured."""

from __future__ import annotations

import json
from typing import Any

from glmkit.mock import engine
from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.defects._wire_common import answer_anyway
from glmkit.mock.types import ErrorReply, MockContext, Reply, ToolCallOut


def _user_text(ctx: MockContext) -> str:
    return next((m.text for m in reversed(ctx.req.messages) if m.role == "user"), "") if ctx.req else ""


def _set_reasoning(reply: Reply, text: str | None) -> None:
    old_r = engine.toks(reply.reasoning)
    reply.reasoning = text
    new_r = engine.toks(text)
    reply.usage.completion_tokens += new_r - old_r
    reply.usage.reasoning_tokens = new_r


@register_defect
class EffortIgnored(Defect):
    name = "effort-ignored"
    title = "reasoning_effort has no effect: every effort level thinks as little as low"
    sources = ("https://docs.z.ai/guides/llm/glm-5.3.md",)
    cases = ("WIRE-003",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if reply.reasoning:
            _set_reasoning(reply, engine._reasoning(_user_text(ctx), "low"))
        return reply


@register_defect
class InvalidEffortAccepted(Defect):
    name = "invalid-effort-accepted"
    title = "reasoning_effort values the docs reject (medium, xhigh, none) are silently accepted with 200"
    sources = ("https://docs.z.ai/guides/capabilities/thinking.md",)
    cases = ("WIRE-004",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason == "bad_effort":
            return answer_anyway(ctx) or err
        return err


@register_defect
class EffortNoneStillThinks(Defect):
    name = "effort-none-still-thinks"
    title = "glm-5.2 with reasoning_effort none still returns reasoning"
    sources = ("https://docs.z.ai/guides/capabilities/thinking.md",)
    cases = ("WIRE-005",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if ctx.req and str(ctx.req.effort).lower() in ("none", "minimal") and reply.reasoning is None:
            _set_reasoning(reply, "Let me think about this carefully before answering.")
        return reply


@register_defect
class ToolChoiceIgnored(Defect):
    name = "tool-choice-ignored"
    title = "tool_choice is ignored: none still calls a tool, required does not, a named function gets the first tool"
    sources = ("https://github.com/zai-org/GLM-5/issues/144",)
    cases = ("WIRE-011",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        req = ctx.req
        if req is None or not req.tools:
            return reply
        first = req.tools[0]
        tc = req.tool_choice
        if tc == "none" and not reply.tool_calls:
            args = json.dumps(engine.example_args(first.parameters, _user_text(ctx)))
            reply.tool_calls = [ToolCallOut(id=f"call_{reply.id[:16]}_0", name=first.name, arguments=args)]
            reply.content, reply.finish_reason = None, "tool_calls"
        elif tc == "required":
            reply.tool_calls, reply.content, reply.finish_reason = [], "I can answer that without a tool.", "stop"
        elif isinstance(tc, dict) and reply.tool_calls:
            reply.tool_calls[0].name = first.name
        return reply


@register_defect
class StopWordsIgnored(Defect):
    name = "stop-words-ignored"
    title = "stop / stop_sequences are ignored: the stop word and the text after it stay in the answer"
    sources = ("https://docs.z.ai/api-reference/llm/chat-completion.md",)
    cases = ("WIRE-012",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if ctx.req and ctx.req.stop and reply.content:
            reply.content = f"{reply.content} {ctx.req.stop[0]} and the rest of the sentence"
        return reply


@register_defect
class JsonModeFenced(Defect):
    name = "json-mode-fenced"
    title = "response_format json_object answers JSON inside a markdown code fence (not parseable as is)"
    sources = ("https://docs.z.ai/guides/capabilities/struct-output.md",)
    cases = ("WIRE-014",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        rf = ctx.body.get("response_format") if isinstance(ctx.body, dict) else None
        if isinstance(rf, dict) and rf.get("type") == "json_object" and reply.content:
            reply.content = f"```json\n{reply.content}\n```"
        return reply


@register_defect
class DoSampleIgnored(Defect):
    name = "do-sample-ignored"
    title = "do_sample false is ignored: every answer differs"
    sources = ("https://docs.z.ai/api-reference/llm/chat-completion.md",)
    cases = ("WIRE-013",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        st = self.state(ctx)
        st["n"] = st.get("n", 0) + 1
        if reply.content:
            reply.content = f"{reply.content} (variant {st['n']})"
        return reply
