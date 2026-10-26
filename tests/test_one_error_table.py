"""Issue 19 guard: the Z.ai business-code table lives only in glmkit.zai_errors.

Fails when another module under src/glmkit holds a second mapping, i.e. a dict literal keyed by three or more
documented Z.ai business codes (as strings or ints), or a ``range()`` over the 13xx usage-limit block.
"""

from __future__ import annotations

import ast
from pathlib import Path

from glmkit import zai_errors

SRC = Path(zai_errors.__file__).parent
CANONICAL = SRC / "zai_errors.py"
CODES = set(zai_errors.TABLE)


def _is_code(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and str(node.value) in CODES and isinstance(node.value, str | int)


def second_mappings(files: list[Path]) -> list[str]:
    hits: list[str] = []
    for f in files:
        if f == CANONICAL:
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict) and sum(_is_code(k) for k in node.keys) >= 3:
                hits.append(f"{f.relative_to(SRC)}:{node.lineno} dict keyed by Z.ai business codes")
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "range"
                and len(node.args) == 2
                and all(isinstance(a, ast.Constant) for a in node.args)
                and node.args[0].value == 1316  # type: ignore[attr-defined]
            ):
                hits.append(f"{f.relative_to(SRC)}:{node.lineno} range over the 13xx usage-limit codes")
    return hits


def test_one_code_table_in_the_package() -> None:
    assert second_mappings(sorted(SRC.rglob("*.py"))) == []


def test_guard_detects_a_planted_second_mapping(tmp_path: Path) -> None:
    planted = SRC / "bridge" / "core" / "_planted_table.py"
    assert not planted.exists()
    try:
        planted.write_text('T = {"1302": 429, "1305": 503, "1308": 429}\n', encoding="utf-8")
        hits = second_mappings(sorted(SRC.rglob("*.py")))
    finally:
        planted.unlink(missing_ok=True)
    assert len(hits) == 1 and "_planted_table.py" in hits[0]
