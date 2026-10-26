"""SQL result equality: run the candidate's SQL and the reference SQL on the same database and compare rows.

The executor is pluggable: ``@sql_executor("name")`` registers ``fn(setup: list[str], sql: str) -> list[tuple]``;
a grader config picks it with ``executor: name``. The default ``sqlite`` builds a fresh in-memory database from the
``setup`` statements, so a candidate query can never alter state another item sees.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from glmkit.eval.graders import Grade, GradeContext, grader
from glmkit.eval.graders.basic import strip_fences

SqlExecutor = Callable[[list[str], str], list[tuple[Any, ...]]]
_EXECUTORS: dict[str, SqlExecutor] = {}


def sql_executor(name: str) -> Callable[[SqlExecutor], SqlExecutor]:
    def deco(fn: SqlExecutor) -> SqlExecutor:
        _EXECUTORS[name] = fn
        return fn

    return deco


@sql_executor("sqlite")
def sqlite_executor(setup: list[str], sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(":memory:")
    try:
        for stmt in setup:
            con.executescript(stmt)
        return [tuple(r) for r in con.execute(sql.strip().rstrip(";")).fetchall()]
    finally:
        con.close()


def _canon(rows: list[tuple[Any, ...]], ordered: bool) -> list[Any]:
    norm = [tuple(round(v, 6) if isinstance(v, float) else v for v in r) for r in rows]
    return norm if ordered else sorted(norm, key=repr)


@grader("sql")
async def sql(ctx: GradeContext) -> Grade:
    """Result-set equality of the candidate SQL and the reference SQL (item.expected). ordered=false by default."""
    name = ctx.config.get("executor", "sqlite")
    if name not in _EXECUTORS:
        raise KeyError(f"unknown sql executor {name!r}; known: {', '.join(_EXECUTORS)}")
    run = _EXECUTORS[name]
    setup = ctx.config.get("setup") or []
    setup = [setup] if isinstance(setup, str) else list(setup)
    ordered = bool(ctx.config.get("ordered", False))
    want = _canon(run(setup, str(ctx.item.expected)), ordered)
    try:
        got = _canon(run(setup, strip_fences(ctx.output)), ordered)
    except Exception as e:  # invalid SQL is a wrong answer, not a harness error
        return Grade(0.0, False, f"candidate SQL failed: {type(e).__name__}: {e}"[:200], {"stage": "execute"})
    ok = got == want
    return Grade(1.0 if ok else 0.0, ok, "same result set" if ok else f"{len(got)} rows vs {len(want)} expected")
