"""STRM-001/002 planted defect: the streamed tool-call arguments differ from the non-streamed ones. Typed values
become strings (``123`` -> ``"123"``), as in sglang#35564; non-stream stays correct."""

from __future__ import annotations

import json
from typing import Any

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, MockEvent


def stringify(v: Any) -> Any:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, int | float):
        return str(v)
    if isinstance(v, dict):
        return {k: stringify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [stringify(x) for x in v]
    return v


def _slot(e: MockEvent, anthropic: bool) -> tuple[dict[str, Any], str, int] | None:
    """(dict holding the fragment, its key, the tool-call index) for an argument-fragment event."""
    d = e.data
    if not isinstance(d, dict):
        return None
    if anthropic:
        delta = d.get("delta") or {}
        return (delta, "partial_json", d.get("index", 0)) if delta.get("type") == "input_json_delta" else None
    for ch in d.get("choices") or []:
        for tc in (ch.get("delta") or {}).get("tool_calls") or []:
            if (tc.get("function") or {}).get("arguments"):
                return tc["function"], "arguments", tc.get("index", 0)
    return None


@register_defect
class StreamArgsMismatch(Defect):
    name = "stream-args-mismatch"
    title = "streamed tool-call arguments stringify typed values (int/bool/null become strings)"
    sources = ("https://github.com/sgl-project/sglang/issues/35564",)
    cases = ("STRM-001", "STRM-002")

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        anthropic = ctx.protocol == "anthropic_messages"
        groups: dict[int, list[tuple[dict[str, Any], str]]] = {}
        for e in events:
            slot = _slot(e, anthropic)
            if slot is not None:
                groups.setdefault(slot[2], []).append((slot[0], slot[1]))
        for frags in groups.values():
            raw = "".join(box[k] for box, k in frags)
            try:
                fixed = json.dumps(stringify(json.loads(raw)))
            except ValueError:
                continue
            for i, (box, k) in enumerate(frags):
                box[k] = fixed if i == 0 else ""
        return events
