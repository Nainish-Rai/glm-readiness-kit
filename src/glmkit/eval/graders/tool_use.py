"""``tool_use`` grader: did the model make the expected tool call (name and arguments)?

``expected`` is a list of ``{"name": ..., "arguments": {...}}``. The output is what the eval records: the model's
text, or, when it answered with tool calls only, a JSON list of ``{"name", "arguments"}``. Config ``mode``:
``first`` (default: the first call must match), ``sequence`` (the calls must match in order, extra calls fail),
``any`` (every expected call appears somewhere). Arguments match when every expected key has an equal value in
the call (extra keys are allowed; strings compare after trimming, numbers numerically).
"""

from __future__ import annotations

import json
from typing import Any

from glmkit.eval.graders import Grade, GradeContext, grader
from glmkit.eval.graders.basic import strip_fences


def parse_calls(output: str) -> list[dict[str, Any]]:
    text = strip_fences(output)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return []
    if isinstance(data, dict):
        data = [data]
    calls = []
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or "name" not in c:
            continue
        args = c.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append({"name": c["name"], "arguments": args if isinstance(args, dict) else {}})
    return calls


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) < 1e-9
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def call_matches(want: dict[str, Any], got: dict[str, Any]) -> tuple[bool, str]:
    if want["name"] != got["name"]:
        return False, f"called {got['name']!r}, expected {want['name']!r}"
    for k, v in (want.get("arguments") or {}).items():
        if k not in got["arguments"]:
            return False, f"{want['name']}: missing argument {k!r}"
        if not _eq(v, got["arguments"][k]):
            return False, f"{want['name']}: argument {k!r} is {got['arguments'][k]!r}, expected {v!r}"
    return True, "match"


@grader("tool_use")
async def tool_use(ctx: GradeContext) -> Grade:
    """Expected tool call(s) made with the right name and arguments (mode: first | sequence | any)."""
    exp = ctx.config.get("expected", ctx.item.expected)
    want = exp if isinstance(exp, list) else [exp]
    got = parse_calls(ctx.output)
    if not got:
        return Grade(0.0, False, "no tool call in the output", {"calls": 0})
    mode = ctx.config.get("mode", "first")
    if mode == "first":
        ok, why = call_matches(want[0], got[0])
    elif mode == "sequence":
        if len(got) != len(want):
            ok, why = False, f"{len(got)} calls, expected {len(want)}"
        else:
            results = [call_matches(w, g) for w, g in zip(want, got, strict=True)]
            bad = next((r for r in results if not r[0]), None)
            ok, why = (bad is None), (bad[1] if bad else "match")
    elif mode == "any":
        missing = [w["name"] for w in want if not any(call_matches(w, g)[0] for g in got)]
        ok, why = (not missing), (f"not called: {', '.join(missing)}" if missing else "match")
    else:
        raise ValueError(f"tool_use: unknown mode {mode!r}")
    return Grade(1.0 if ok else 0.0, ok, why, {"calls": len(got)})
