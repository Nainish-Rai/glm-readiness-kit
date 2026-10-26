"""Repair of one tool call: parse/repair the arguments, map flattened-schema arguments back, validate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from glmkit.bridge.core.jsonrepair import ArgumentsUnrepairable, repair_arguments
from glmkit.bridge.core.retry import ToolFailure
from glmkit.bridge.core.schema import unflatten_args, validation_issues

if TYPE_CHECKING:
    from glmkit.bridge.core.pipeline import RewriteContext

HARD_VALIDATORS = {"required", "oneOf", "anyOf"}  # missing/unsatisfiable arguments: worth one retry


@dataclass
class CallOutcome:
    arguments: str
    failure: ToolFailure | None = None


def fix_call(
    ctx: RewriteContext, name: str, raw: Any, *, index: int = 0, call_id: str | None = None, emit: bool = True
) -> CallOutcome:
    """Return the (possibly repaired) arguments string, or a ``ToolFailure`` when no safe repair exists.

    Nothing is substituted: ``{}`` is produced only when the arguments are empty *and* the tool's schema accepts
    ``{}``. Events are emitted into ``ctx`` unless ``emit`` is false.
    """
    schema = ctx.tool_schemas.get(name)
    raw_str = raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else ""
    fixes: list[str] = []
    try:
        obj, fixes = repair_arguments(raw)
    except ArgumentsUnrepairable as e:
        if not raw_str.strip() and not validation_issues(schema, {}):
            obj, fixes = {}, ["empty-arguments"]
        else:
            return CallOutcome(raw_str, ToolFailure(index, call_id, name, raw_str, e.parse_error))
    changed_by_flatten = False
    if name in ctx.flattened:
        obj, changed_by_flatten = unflatten_args(ctx.flattened[name], obj)
    issues = validation_issues(schema, obj)
    out = raw_str if not fixes and not changed_by_flatten else json.dumps(obj, ensure_ascii=False)
    if emit:
        if fixes:
            ctx.emit(
                "tool-args-repair",
                f"{len(raw_str)} chars, not a valid JSON object",
                f"valid JSON object, {len(out)} chars",
                details={"tool": name, "index": index, "repairs": fixes},
                content={"before": raw_str, "after": out},
            )
        if changed_by_flatten:
            ctx.emit(
                "schema-args-unflatten",
                "arguments for the flattened schema",
                "arguments for one branch of the original schema",
                details={"tool": name, "index": index},
                content={"before": raw_str, "after": out},
            )
    hard = [m for v, m in issues if v in HARD_VALIDATORS]
    if hard:
        return CallOutcome(
            out,
            ToolFailure(
                index, call_id, name, out, "arguments violate the tool schema: " + "; ".join(hard), kind="schema"
            ),
        )
    if issues and emit:
        ctx.emit(
            "tool-args-schema-mismatch",
            "arguments parse",
            "arguments passed through unchanged",
            details={"tool": name, "index": index, "issues": [m for _, m in issues]},
        )
    return CallOutcome(out)


def normalize_reasoning_keys(ctx: RewriteContext, obj: dict[str, Any]) -> bool:
    """Rename a ``reasoning`` field to ``reasoning_content`` in a message or delta. True if renamed."""
    if "reasoning" in obj and "reasoning_content" not in obj and isinstance(obj["reasoning"], str):
        obj["reasoning_content"] = obj.pop("reasoning")
        return True
    return False
