"""Streaming defects: a stream that hangs, ends badly or orders its parts wrongly."""

from __future__ import annotations

from typing import Any

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, MockEvent

HANG_S = 3600.0  # far beyond any stream timeout; the client gives up and the server drops the connection


def _delta(e: MockEvent) -> dict[str, Any]:
    d = e.data
    if isinstance(d, dict) and d.get("choices"):
        return d["choices"][0].get("delta") or {}
    return {}


@register_defect
class HangingStream(Defect):
    name = "hanging-stream"
    title = "the stream stalls after a few events and never sends the finish chunk or [DONE]"
    sources = ("https://github.com/ollama/ollama/issues/18926",)
    cases = ("WIRE-015", "WIRE-017")

    AFTER = 3  # events delivered before the stall

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        if len(events) > self.AFTER:
            events[self.AFTER].delay_s = HANG_S
        return events


@register_defect
class StreamMalformed(Defect):
    name = "stream-malformed"
    title = "stream ends with two finish events, no usage on the last chunk and a repeated role chunk"
    sources = ("https://docs.z.ai/guides/capabilities/streaming.md",)
    cases = ("WIRE-015",)

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        if ctx.protocol == "anthropic_messages":
            i = next((k for k, e in enumerate(events) if e.event == "message_delta"), None)
            if i is not None:
                events.insert(i + 1, MockEvent(dict(events[i].data), event="message_delta"))
                events[i].data = {**events[i].data, "usage": {}}
                events[i + 1].data = {**events[i + 1].data, "usage": {}}
            return events
        out: list[MockEvent] = []
        for e in events:
            d = e.data
            if isinstance(d, dict) and d.get("choices") and d["choices"][0].get("finish_reason"):
                d = {k: v for k, v in d.items() if k != "usage"}
                e = MockEvent(d)
                out.append(MockEvent({**d, "choices": [{"index": 0, "delta": {"role": "assistant"}}]}))
                out.append(e)
                out.append(MockEvent(d))  # a second finish chunk
                continue
            out.append(e)
        return out


@register_defect
class ReasoningAfterContent(Defect):
    name = "reasoning-after-content"
    title = "stream sends answer text before the reasoning (reasoning_content / thinking arrives after content)"
    sources = ("https://docs.z.ai/guides/capabilities/thinking-mode.md",)
    cases = ("WIRE-016",)

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        if ctx.protocol == "anthropic_messages":
            idx = [i for i, e in enumerate(events) if e.event in ("content_block_delta",)]
            think = [i for i in idx if events[i].data["delta"]["type"] == "thinking_delta"]
            text = [i for i in idx if events[i].data["delta"]["type"] == "text_delta"]
        else:
            idx = [i for i, e in enumerate(events) if _delta(e)]
            think = [i for i in idx if _delta(events[i]).get("reasoning_content")]
            text = [i for i in idx if _delta(events[i]).get("content")]
        if think and text:  # reasoning slots come first; refill the same slots with text payloads, then reasoning
            slots = sorted(think + text)
            payloads = [events[i].data for i in text] + [events[i].data for i in think]
            for slot, data in zip(slots, payloads, strict=True):
                events[slot].data = data
        return events
