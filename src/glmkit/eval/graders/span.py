"""``span`` grader: SQuAD-style token F1 between the quoted span and the gold span (CUAD clause extraction).

Config: ``min_f1`` (default 0.5) is the pass line; the score is the F1 itself. ``expected`` is one gold span or a
list of acceptable spans (the best F1 counts). An empty gold list means "no such clause": the answer passes only if
it says so (``none`` / ``no such clause`` / empty).
"""

from __future__ import annotations

import re
from collections import Counter

from glmkit.eval.graders import Grade, GradeContext, grader

_TOKEN = re.compile(r"[a-z0-9]+")
_NONE = re.compile(r"^\W*(none|no such clause|not present|no clause|n/?a)\W*$", re.I)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def f1(pred: str, gold: str) -> float:
    p, g = tokens(pred), tokens(gold)
    if not p or not g:
        return float(p == g)
    common = sum((Counter(p) & Counter(g)).values())
    if common == 0:
        return 0.0
    prec, rec = common / len(p), common / len(g)
    return 2 * prec * rec / (prec + rec)


@grader("span")
async def span(ctx: GradeContext) -> Grade:
    """Token-F1 of the answer against the gold span(s); passes at min_f1 (default 0.5). Score = best F1."""
    exp = ctx.config.get("expected", ctx.item.expected)
    golds = [] if exp is None else (exp if isinstance(exp, list) else [exp])
    out = ctx.output.strip().strip("\"'`")
    if not golds:
        ok = not out or bool(_NONE.match(out))
        return Grade(
            1.0 if ok else 0.0, ok, "correctly reported no clause" if ok else "claimed a clause that is absent"
        )
    best = max(f1(out, str(g)) for g in golds)
    ok = best >= float(ctx.config.get("min_f1", 0.5))
    return Grade(best, ok, f"token F1 {best:.2f}", {"f1": round(best, 4)})
