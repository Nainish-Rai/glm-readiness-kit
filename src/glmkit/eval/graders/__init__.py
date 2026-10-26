"""Graders: score one model output in [0, 1]. Register with ``@grader("name")`` in a module of this package (or
a plugin module); an item or task type selects one with ``grader: {type: name, ...config}``.

A grader is ``async def fn(ctx: GradeContext) -> Grade``. Deterministic graders never touch the network; the
``judge`` grader reaches its judge through ``ctx.judge``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from glmkit.eval.goldenset import GoldenItem
from glmkit.plugins import import_submodules, load_plugins


@dataclass
class Grade:
    score: float
    passed: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)  # grader specific (judge verdicts, parsed values ...)


@dataclass
class GradeContext:
    item: GoldenItem
    config: dict[str, Any]
    output: str  # the candidate's final text (tool calls are rendered as JSON when there is no text)
    target: str  # target label being graded
    baseline_output: str | None = None  # same item and replay on the baseline (the judge's comparison)
    is_baseline: bool = False
    replay: int = 0
    judge: Any = None  # glmkit.eval.graders.judge.JudgeService | None


GraderFn = Callable[[GradeContext], Awaitable[Grade]]
_REGISTRY: dict[str, GraderFn] = {}
_discovered = False


def grader(name: str) -> Callable[[GraderFn], GraderFn]:
    def deco(fn: GraderFn) -> GraderFn:
        if name in _REGISTRY and _REGISTRY[name] is not fn:
            raise ValueError(f"grader {name!r} registered twice")
        _REGISTRY[name] = fn
        return fn

    return deco


def discover() -> dict[str, GraderFn]:
    global _discovered
    if not _discovered:
        import_submodules(__name__)
        _discovered = True
    load_plugins()
    return dict(sorted(_REGISTRY.items()))


async def grade(ctx: GradeContext) -> Grade:
    reg = discover()
    kind = ctx.config.get("type", "exact")
    if kind not in reg:
        raise KeyError(f"unknown grader {kind!r}; known: {', '.join(reg)}")
    try:
        return await reg[kind](ctx)
    except Exception as e:  # a broken grader config must surface on the item, not crash the run
        return Grade(0.0, False, f"grader {kind!r} raised {type(e).__name__}: {e}", {"error": True})
