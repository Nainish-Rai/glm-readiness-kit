"""Planted defects on when and how tool calls are produced (TOOL-001, 002, 007, 011, 013)."""

from __future__ import annotations

from typing import Any

from starlette.responses import PlainTextResponse

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.engine import toks
from glmkit.mock.types import ErrorReply, MockContext, Reply, ToolCallOut


@register_defect
class ToolMarkerLeak(Defect):
    name = "tool-marker-leak"
    title = "tool-call markup (<tool_call>, <arg_key>) leaks into reasoning_content while the call is also emitted"
    sources = ("https://github.com/anomalyco/opencode/issues/6708",)
    cases = ("TOOL-011",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if reply.tool_calls:
            tc = reply.tool_calls[0]
            leak = f"<tool_call>{tc.name}<arg_key>x</arg_key><arg_value>y</arg_value></tool_call>"
            reply.reasoning = ((reply.reasoning or "") + " " + leak).strip()
        return reply


@register_defect
class FlakyToolCall(Defect):
    name = "flaky-tool-call"
    title = "every 4th request that should call a tool answers in text instead (75% trigger rate)"
    sources = ("https://openrouter.ai/blog/announcements/provider-variance-introducing-exacto/",)
    cases = ("TOOL-001",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if not reply.tool_calls:
            return reply
        st = self.state(ctx)
        st["n"] = st.get("n", 0) + 1
        if st["n"] % 4 == 0:
            reply.tool_calls, reply.content, reply.finish_reason = [], "I'll take care of that now.", "stop"
        return reply


@register_defect
class ToolsAlwaysCalled(Defect):
    name = "tools-always-called"
    title = "a tool is called even when the question needs none (no irrelevance detection)"
    sources = ("https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard",)
    cases = ("TOOL-002", "TOOL-015")

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        req = ctx.req
        assert req is not None
        if req.tools and not reply.tool_calls and reply.finish_reason == "stop" and req.messages[-1].role == "user":
            t = req.tools[0]
            reply.tool_calls = [ToolCallOut(id=f"call_{reply.id[:16]}_0", name=t.name, arguments="{}")]
            reply.content, reply.finish_reason = None, "tool_calls"
        return reply


@register_defect
class LongContextToolDrop(Defect):
    name = "long-context-tool-drop"
    title = "above ~50k tokens of history the model stops with a one-sentence reply instead of calling the tool"
    sources = ("https://github.com/vllm-project/vllm/issues/56868",)
    cases = ("TOOL-013",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        if reply.tool_calls and toks(ctx.raw.decode("utf-8", "ignore")) > 50_000:
            reply.tool_calls, reply.content, reply.finish_reason = [], "Understood, I will do that.", "stop"
        return reply


@register_defect
class ManyToolsBroken(Defect):
    name = "many-tools-broken"
    title = "with more than 64 tools the wrong tool is picked; a 129th tool crashes the host instead of 400"
    sources = ("https://docs.z.ai/api-reference/llm/chat-completion.md",)
    cases = ("TOOL-007",)

    def on_error(self, ctx: MockContext, err: ErrorReply) -> Any:
        if err.reason == "too_many_tools":
            return PlainTextResponse(
                'Traceback (most recent call last):\n  File "/srv/gateway/tools.py", line 9, in pack\n'
                "    slot = TOOL_SLOTS[len(tools)]\nIndexError: list index out of range\n",
                status_code=500,
            )
        return err

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        req = ctx.req
        assert req is not None
        if len(req.tools) > 64:
            for tc in reply.tool_calls:
                tc.name = req.tools[0].name
        return reply
