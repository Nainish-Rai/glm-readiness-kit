"""STRM-005 planted defect: the usage reported at the end of a stream differs from the non-stream usage for the
same request (the GLM-5#139 family: stream usage does not match)."""

from __future__ import annotations

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, MockEvent


@register_defect
class StreamUsageDrift(Defect):
    name = "stream-usage-drift"
    title = "streamed usage reports 25% more prompt tokens than the non-stream response"
    sources = ("https://github.com/zai-org/GLM-5/issues/139",)
    cases = ("STRM-005",)

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        for e in events:
            d = e.data
            if not isinstance(d, dict) or not isinstance(d.get("usage"), dict):
                continue
            u = d["usage"]
            key = "prompt_tokens" if "prompt_tokens" in u else "input_tokens"
            if isinstance(u.get(key), int) and u[key] > 0:
                u[key] = int(u[key] * 1.25) + 1
        return events
