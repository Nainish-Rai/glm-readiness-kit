"""How the mock decides which tool calls a well-behaved GLM makes (used by ``engine.generate``).

The mock is deterministic and has no model, so it follows simple, documented conventions that the tool
conformance cases rely on:

- Tools named in the user text are called, in order of appearance (two names = parallel calls).
- ``<tool> ... with exactly these arguments: {json}`` makes the call carry exactly that JSON object (a faithful
  model copies strings, numbers and nulls verbatim). Without it, schema-satisfying example arguments are used.
- A text question that names no tool ("What is 2+2?") is answered in text (irrelevance).
- Otherwise the first tool is called (the original mock behaviour).
"""

from __future__ import annotations

import json
import re
from typing import Any

from glmkit.mock.types import CanonRequest, Tool

QUESTION = re.compile(r"^\s*(what|who|why|how|when|where|which|is|are|does|do|can)\b[^\n]*\?", re.I)
EXACT = re.compile(r"exactly these arguments:\s*", re.I)


def _named(req: CanonRequest, text: str) -> list[Tool]:
    hits: list[tuple[int, Tool]] = []
    for t in req.tools:
        if not t.name:
            continue
        m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(t.name)}(?![A-Za-z0-9_])", text)
        if m:
            hits.append((m.start(), t))
    return [t for _, t in sorted(hits, key=lambda h: h[0])]


def _relevant(req: CanonRequest, text: str) -> list[Tool]:
    """Tools whose name parts or longer description words appear in the text ("weather" -> get_weather)."""
    words = set(re.findall(r"[a-z]{4,}", text.lower()))
    hits = []
    for t in req.tools:
        vocab = {w for w in re.split(r"[^a-z]+", (t.name or "").lower()) if len(w) >= 4}
        vocab |= set(re.findall(r"[a-z]{5,}", (t.description or "").lower()))
        if vocab & words:
            hits.append(t)
    return hits


def exact_arguments(tool: Tool, text: str, *, only_tool: bool) -> dict[str, Any] | None:
    """The JSON object the prompt says to pass to ``tool`` (``<name> ... exactly these arguments: {...}``)."""
    dec = json.JSONDecoder()
    for m in EXACT.finditer(text):
        before = text[max(0, m.start() - 120) : m.start()]
        if only_tool or tool.name in before:
            try:
                obj, _ = dec.raw_decode(text, m.end())
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def plan_calls(req: CanonRequest, text: str) -> list[tuple[Tool, dict[str, Any] | None]]:
    """Tool calls for the latest user turn: ``(tool, exact_args_or_None)`` pairs; empty = answer in text."""
    if not req.tools or req.tool_choice == "none":
        return []
    if isinstance(req.tool_choice, dict) and req.tool_choice.get("name"):
        tool = next((t for t in req.tools if t.name == req.tool_choice["name"]), req.tools[0])
        return [(tool, exact_arguments(tool, text, only_tool=True))]
    named = _named(req, text)
    if named:
        return [(t, exact_arguments(t, text, only_tool=len(named) == 1)) for t in named]
    relevant = _relevant(req, text)
    if req.tool_choice != "required" and QUESTION.match(text) and not relevant:
        return []
    tool = relevant[0] if relevant else req.tools[0]
    return [(tool, exact_arguments(tool, text, only_tool=True))]
