"""Aggregate checks: evaluated once per item over all attempts (``repeats``)."""

from __future__ import annotations

from glmkit.conform.checks import CheckContext, CheckOutcome, check, compare


@check("pass_rate", aggregate=True)
def pass_rate(ctx: CheckContext) -> CheckOutcome:
    """Share of non-throttled attempts whose per-attempt checks all passed, compared with ``gte``.
    Slice 02 extends statistical reporting (Wilson CI) on top of this."""
    scored = [a for a in ctx.attempts if a.status != "throttled"]
    if not scored:
        return CheckOutcome("skip", "every attempt was throttled")
    rate = sum(1 for a in scored if a.status == "pass") / len(scored)
    out = compare(round(rate, 4), ctx.params)
    out.message = f"{rate:.0%} of {len(scored)} attempts passed; " + out.message
    return out
