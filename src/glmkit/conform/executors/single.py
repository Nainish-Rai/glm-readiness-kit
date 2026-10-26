"""Default executor: send the case request once in the requested mode."""

from __future__ import annotations

from glmkit.conform.executors import ExecContext, executor
from glmkit.contracts.evidence import Exchange


@executor("single")
async def single(ctx: ExecContext) -> list[Exchange]:
    return [await ctx.client.send_canonical(ctx.request, stream=ctx.stream, label="main")]
