"""``sql_db`` grader: run the candidate SQL against a bundled SQLite database file, opened read-only.

Config: ``database`` (``"shop"`` for the demo database that ships with the kit, or a path to a ``.sqlite`` file),
``ordered`` (default false). Candidate and reference SQL (item.expected) must return the same rows. Writes are
impossible (``mode=ro``, ``query_only``), and a query that runs too long is aborted: both count as a wrong answer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from glmkit.eval.graders import Grade, GradeContext, grader
from glmkit.eval.graders.basic import strip_fences

MAX_VM_STEPS = 20_000_000  # progress-handler ticks of 1000 VM instructions each below


def resolve_database(name: str) -> Path:
    if name == "shop":
        from glmkit.workbench.shop import DB_PATH

        return DB_PATH
    p = Path(name)
    if not p.is_file():
        raise FileNotFoundError(f"sql_db: database {name!r} not found")
    return p


def run_readonly(db: Path, sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        ticks = {"n": 0}

        def guard() -> int:
            ticks["n"] += 1
            return 1 if ticks["n"] * 1000 > MAX_VM_STEPS else 0

        con.set_progress_handler(guard, 1000)
        return [tuple(r) for r in con.execute(sql.strip().rstrip(";")).fetchall()]
    finally:
        con.close()


def _canon(rows: list[tuple[Any, ...]], ordered: bool) -> list[Any]:
    norm = [tuple(round(v, 6) if isinstance(v, float) else v for v in r) for r in rows]
    return norm if ordered else sorted(norm, key=repr)


@grader("sql_db")
async def sql_db(ctx: GradeContext) -> Grade:
    """Result-set equality on a bundled read-only SQLite file (database: shop | path). ordered=false by default."""
    db = resolve_database(str(ctx.config.get("database", "shop")))
    ordered = bool(ctx.config.get("ordered", False))
    want = _canon(run_readonly(db, str(ctx.item.expected)), ordered)
    try:
        got = _canon(run_readonly(db, strip_fences(ctx.output)), ordered)
    except Exception as e:  # invalid SQL, a write attempt or a runaway query is a wrong answer
        return Grade(0.0, False, f"candidate SQL failed: {type(e).__name__}: {e}"[:200], {"stage": "execute"})
    ok = got == want
    return Grade(1.0 if ok else 0.0, ok, "same result set" if ok else f"{len(got)} rows vs {len(want)} expected")
