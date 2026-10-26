"""Planted defects on tool call identity (TOOL-003, 008, 012)."""

from __future__ import annotations

import re

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply


@register_defect
class DuplicateToolIds(Defect):
    name = "duplicate-tool-ids"
    title = "parallel tool calls share one id, so tool results cannot be matched (crush#3153, canitoolcall probe)"
    sources = ("https://github.com/charmbracelet/crush/issues/3153", "https://github.com/redd34/canitoolcall")
    cases = ("TOOL-003", "TOOL-012")

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        for tc in reply.tool_calls[1:]:
            tc.id = reply.tool_calls[0].id
        return reply


@register_defect
class UnknownToolName(Defect):
    name = "unknown-tool-name"
    title = "the returned tool name is not one of the offered tools (namespaces and punctuation rewritten)"
    sources = (
        "https://openrouter.ai/blog/announcements/provider-variance-introducing-exacto/",
        "https://github.com/vllm-project/vllm/blob/main/tests/tool_parsers/test_glm47_moe_tool_parser.py",
    )
    cases = ("TOOL-008",)

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        for tc in reply.tool_calls:
            tc.name = re.sub(r"[.\-]", "_", tc.name).replace("__", ".")
        return reply
