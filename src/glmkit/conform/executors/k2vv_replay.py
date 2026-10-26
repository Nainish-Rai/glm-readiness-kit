"""``k2vv_replay``: replay a K2VV / Prism style JSONL corpus (see ``glmkit.conform.k2vv``).

executor_config: ``corpus`` (path), ``limit``, ``reference`` (K2VV results.jsonl of the official API), ``max_tokens``.
"""

from __future__ import annotations

from glmkit.conform import k2vv
from glmkit.conform.executors import ExecContext, executor
from glmkit.contracts.evidence import Exchange


@executor("k2vv_replay")
async def k2vv_replay(ctx: ExecContext) -> list[Exchange]:
    cfg = ctx.config
    ref = k2vv.resolve_corpus(cfg["reference"]) if cfg.get("reference") else None
    samples = k2vv.load_corpus(k2vv.resolve_corpus(cfg["corpus"]), limit=cfg.get("limit"), reference=ref)
    return await k2vv.replay(ctx.client, samples, stream=ctx.stream, max_tokens=int(cfg.get("max_tokens", 512)))
