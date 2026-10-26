"""Deterministic graders: exact, regex, numeric, json_schema."""

from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

from glmkit.eval.graders import Grade, GradeContext, grader

FENCE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\s*\n(.*?)```", re.S)
NUMBER = re.compile(r"[-+]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def strip_fences(text: str) -> str:
    m = FENCE.search(text)
    return m.group(1).strip() if m else text.strip()


def _norm(s: Any, cfg: dict[str, Any]) -> str:
    s = str(s)
    if cfg.get("strip", True):
        s = s.strip()
    if cfg.get("normalize_space", True):
        s = re.sub(r"\s+", " ", s)
    if cfg.get("ignore_case", False):
        s = s.casefold()
    if cfg.get("strip_punct", False):
        s = s.strip(" .!?,;:\"'`")
    return s


def _expected_list(ctx: GradeContext) -> list[Any]:
    exp = ctx.config.get("expected", ctx.item.expected)
    if exp is None:
        raise ValueError("no expected value (set item.expected or grader.expected)")
    return exp if isinstance(exp, list) else [exp]


@grader("exact")
async def exact(ctx: GradeContext) -> Grade:
    """Normalized string equality with the expected answer (any of a list). Options: ignore_case, strip_punct."""
    got = _norm(ctx.output, ctx.config)
    want = [_norm(e, ctx.config) for e in _expected_list(ctx)]
    ok = got in want
    return Grade(1.0 if ok else 0.0, ok, "match" if ok else f"expected {want[0]!r}, got {got[:120]!r}")


@grader("regex")
async def regex(ctx: GradeContext) -> Grade:
    """``re.search(pattern, output)``; pattern from the grader config or the item's expected. must_match=false inverts."""  # noqa: E501
    pattern = ctx.config.get("pattern") or ctx.item.expected
    if not pattern:
        raise ValueError("regex grader needs 'pattern' (or item.expected)")
    flags = sum(getattr(re, f.upper()) for f in ctx.config.get("flags", "") if f.upper() in "IMSX")
    hit = re.search(str(pattern), ctx.output, flags) is not None
    ok = hit == ctx.config.get("must_match", True)
    return Grade(
        1.0 if ok else 0.0, ok, "match" if ok else f"pattern {pattern!r} {'not found' if not hit else 'found'}"
    )


@grader("numeric")
async def numeric(ctx: GradeContext) -> Grade:
    """Number extracted from the output (extract: last | first | whole) within abs_tol / rel_tol of the expected."""
    want = float(str(ctx.config.get("expected", ctx.item.expected)).replace(",", ""))
    mode = ctx.config.get("extract", "last")
    text = ctx.output.strip()
    found = ([text] if NUMBER.fullmatch(text) else []) if mode == "whole" else NUMBER.findall(text)
    if not found:
        return Grade(0.0, False, "no number in output")
    got = float((found[0] if mode == "first" else found[-1]).replace(",", ""))
    tol = max(float(ctx.config.get("abs_tol", 0.0)), float(ctx.config.get("rel_tol", 0.0)) * abs(want))
    ok = abs(got - want) <= tol + 1e-12
    return Grade(1.0 if ok else 0.0, ok, f"got {got}, expected {want} (tol {tol:g})", {"value": got})


def _subset(want: Any, got: Any) -> bool:
    if isinstance(want, dict):
        return isinstance(got, dict) and all(k in got and _subset(v, got[k]) for k, v in want.items())
    if isinstance(want, list):
        return (
            isinstance(got, list)
            and len(want) == len(got)
            and all(_subset(w, g) for w, g in zip(want, got, strict=True))
        )
    return want == got


@grader("json_schema")
async def json_schema(ctx: GradeContext) -> Grade:
    """Output parses as JSON (code fences stripped) and validates against ``schema``. With ``match_expected: true``
    the parsed value must also contain the item's expected object (a subset match)."""
    schema = ctx.config.get("schema")
    if schema is None:
        raise ValueError("json_schema grader needs 'schema'")
    try:
        value = json.loads(strip_fences(ctx.output))
    except ValueError as e:
        return Grade(0.0, False, f"not valid JSON: {e}", {"stage": "parse"})
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        return Grade(
            0.0, False, f"schema violation at /{'/'.join(map(str, e.path))}: {e.message[:160]}", {"stage": "schema"}
        )
    if ctx.config.get("match_expected") and not _subset(ctx.item.expected, value):
        return Grade(0.0, False, "valid against the schema but differs from expected", {"stage": "expected"})
    return Grade(1.0, True, "valid")
