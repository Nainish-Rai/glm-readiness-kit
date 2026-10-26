"""Aggregate counts over SwitchTrace JSONL files (used by ``traces stats`` and the dashboard envelope)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from glmkit.contracts.trace import SwitchTrace

TOKEN_FIELDS = (
    "input_tokens_total",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens_total",
    "reasoning_tokens",
)


def is_replayable(t: SwitchTrace) -> bool:
    """Replayable = the prompt content is present, so the eval engine can send it to another model."""
    return t.privacy.content_present and bool(t.request.messages)


def compute_stats(traces: Iterable[SwitchTrace]) -> dict[str, Any]:
    by_source: Counter[str] = Counter()
    by_task: Counter[str] = Counter()
    tokens: dict[str, dict[str, int]] = {}
    replayable = baseline = redacted = 0
    cost = 0.0
    cost_rows = 0
    for t in traces:
        k = t.source.kind
        by_source[k] += 1
        by_task[t.task_type or "unlabelled"] += 1
        if is_replayable(t):
            replayable += 1
        else:
            baseline += 1
        redacted += t.privacy.redacted
        bucket = tokens.setdefault(k, dict.fromkeys(TOKEN_FIELDS, 0))
        for f in TOKEN_FIELDS:
            bucket[f] += getattr(t.usage, f) or 0
        if t.cost_usd_reported is not None:
            cost += t.cost_usd_reported
            cost_rows += 1
    total = sum(by_source.values())
    return {
        "total": total,
        "by_source": dict(by_source.most_common()),
        "by_task_type": dict(by_task.most_common()),
        "replayable": replayable,
        "baseline_only": baseline,
        "redacted": redacted,
        "tokens_by_source": tokens,
        "tokens_total": {f: sum(b[f] for b in tokens.values()) for f in TOKEN_FIELDS},
        "cost_usd_reported": round(cost, 6) if cost_rows else None,
    }


def load_traces(path: Any) -> Iterable[SwitchTrace]:
    from pathlib import Path

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield SwitchTrace.model_validate_json(line)
