"""LAT-001's planted defect: a flat time-to-first-token penalty whenever the request carries ``tools``, even with
``tool_choice: none`` (GLM-5#144 reports about 3 s regardless of the number of tools)."""

from __future__ import annotations

from typing import ClassVar

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, MockEvent


@register_defect
class ToolTtftPenalty(Defect):
    name = "tool-ttft-penalty"
    title = "streamed requests with tools wait a flat extra time before the first token"
    sources = ("https://github.com/zai-org/GLM-5/issues/144",)
    cases = ("LAT-001",)
    penalty_s: ClassVar[float] = 1.5  # the upstream report is ~3 s; smaller keeps runs short, still above the 1 s gate

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        if ctx.req is not None and ctx.req.tools and events:
            events[0].delay_s += self.penalty_s
        return events
