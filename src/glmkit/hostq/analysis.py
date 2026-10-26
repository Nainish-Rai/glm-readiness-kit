"""Pure analysis of exchanges for the host-quality gates. Exchange labels carry the structure:

    warmup | lat.<condition> | eff.<effort> | id.<name> | act.<prompt> | txt.<prompt> | grd.<prompt> | can.<id>

Each executor attempt is one list of exchanges, so "attempt index" is the sample index for repeated probes.
Throttled (HTTP 429) and failed exchanges never enter a statistic; they are counted separately.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from typing import Any

from glmkit.contracts.evidence import Exchange
from glmkit.hostq import stats
from glmkit.hostq.probes import ProbeSet, canary_items, score_canary

Attempts = Sequence[Sequence[Exchange]]


def is_throttled(ex: Exchange) -> bool:
    return ex.normalized.http_status == 429


def is_usable(ex: Exchange) -> bool:
    return ex.normalized.http_status == 200 and ex.transport_error is None


def _labelled(attempts: Attempts, prefix: str) -> list[tuple[int, str, Exchange]]:
    out = []
    for i, exs in enumerate(attempts):
        for ex in exs:
            if ex.label.startswith(prefix):
                out.append((i, ex.label[len(prefix) :], ex))
    return out


def counts(exchanges: Sequence[Exchange]) -> dict[str, int]:
    return {
        "requests": len(exchanges),
        "throttled": sum(1 for e in exchanges if is_throttled(e)),
        "errors": sum(1 for e in exchanges if not is_usable(e) and not is_throttled(e)),
    }


def tokens_per_second(ex: Exchange) -> dict[str, float | None]:
    """Decode rate (completion tokens over the time after the first token) and end-to-end rate."""
    n = ex.normalized.usage.completion_tokens
    t, total = ex.timing.ttft_ms, ex.timing.total_ms
    if not n or not total:
        return {"decode": None, "e2e": None}
    decode = n / ((total - t) / 1000) if t is not None and total - t > 1 else None
    return {"decode": decode, "e2e": n / (total / 1000)}


# ------------------------------------------------------------------ latency


def latency_by_label(attempts: Attempts, prefix: str) -> dict[str, dict[str, Any]]:
    """Per condition (label suffix): TTFT/total in seconds, tokens/s, summaries and throttle counts."""
    groups: dict[str, list[Exchange]] = {}
    for _, key, ex in _labelled(attempts, prefix):
        groups.setdefault(key, []).append(ex)
    out: dict[str, dict[str, Any]] = {}
    for key, exs in groups.items():
        ok = [e for e in exs if is_usable(e) and e.timing.ttft_ms is not None]
        ttft = [e.timing.ttft_ms / 1000 for e in ok]
        total = [e.timing.total_ms / 1000 for e in ok if e.timing.total_ms is not None]
        rates = [tokens_per_second(e) for e in ok]
        decode = [r["decode"] for r in rates if r["decode"] is not None]
        e2e = [r["e2e"] for r in rates if r["e2e"] is not None]
        out[key] = {
            **counts(exs),
            "ttft_s": stats.summarize(ttft),
            "total_s": stats.summarize(total),
            "decode_tps": stats.summarize(decode),
            "e2e_tps": stats.summarize(e2e),
            "completion_tokens": stats.summarize(
                [float(e.normalized.usage.completion_tokens) for e in ok if e.normalized.usage.completion_tokens]
            ),
            "_ttft_values": ttft,
        }
    return out


# ------------------------------------------------------------------ model identity


def model_identity(attempts: Attempts) -> dict[str, Any]:
    """FP-001: a 200 must carry the requested model; an unknown model must be a 4xx, never a 200 from another."""
    rows = []
    for _, key, ex in _labelled(attempts, "id."):
        req_model = (ex.request.body or {}).get("model") if isinstance(ex.request.body, dict) else None
        got = ex.normalized.model
        status = ex.normalized.http_status
        if key == "unknown":
            good = status is not None and 400 <= status < 500
            problem = None if good else f"unknown model got HTTP {status} (answered by {got!r})"
        elif is_throttled(ex):
            continue
        elif status == 200:
            good = got == req_model
            problem = None if good else f"asked for {req_model!r}, response model is {got!r}"
        else:
            good, problem = False, f"HTTP {status}"
        rows.append(
            {"probe": key, "requested": req_model, "returned": got, "status": status, "ok": good, "problem": problem}
        )
    return {"probes": rows, "problems": [r["problem"] for r in rows if r["problem"]]}


# ------------------------------------------------------------------ fingerprint samples


def action_value(ex: Exchange) -> str:
    """The sample for an action probe: first tool called, else '<finish_reason>' (stop, length ...)."""
    calls = ex.normalized.tool_calls
    return calls[0].name or "<unnamed>" if calls else f"<{ex.normalized.finish_reason_norm or 'none'}>"


def action_samples(attempts: Attempts) -> list[list[str]]:
    return [[pid, action_value(ex)] for _, pid, ex in _labelled(attempts, "act.") if is_usable(ex)]


def text_samples(attempts: Attempts) -> list[list[str]]:
    return [[pid, (ex.normalized.content or "")[:64]] for _, pid, ex in _labelled(attempts, "txt.") if is_usable(ex)]


def histogram(samples: Sequence[Sequence[str]]) -> dict[str, int]:
    return dict(sorted(Counter(v for _, v in samples).items()))


def _signature(ex: Exchange) -> str:
    calls = [(c.name, c.arguments_parsed) for c in ex.normalized.tool_calls]
    return json.dumps([calls, ex.normalized.content], sort_keys=True, ensure_ascii=False, default=str)


def greedy_agreement(attempts: Attempts) -> dict[str, Any]:
    """Mean pairwise exact-match agreement of identical temperature-0 requests across attempts."""
    by_prompt: dict[str, list[str]] = {}
    for _, pid, ex in _labelled(attempts, "grd."):
        if is_usable(ex):
            by_prompt.setdefault(pid, []).append(_signature(ex))
    agree = pairs = 0
    for sigs in by_prompt.values():
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                pairs += 1
                agree += sigs[i] == sigs[j]
    lo, hi = stats.wilson(agree, pairs)
    return {
        "agree": agree,
        "pairs": pairs,
        "rate": (agree / pairs) if pairs else None,
        "ci95": [lo, hi] if pairs else None,
        "prompts": len(by_prompt),
        "runs": len(attempts),
    }


# ------------------------------------------------------------------ canaries


def canary_results(attempts: Attempts, ps: ProbeSet) -> dict[str, Any]:
    items = {c["id"]: c for c in canary_items(ps)}
    results: dict[str, bool] = {}
    skipped = 0
    for _, cid, ex in _labelled(attempts, "can."):
        if cid not in items:
            continue
        if not is_usable(ex):
            skipped += 1
            continue
        results[cid] = score_canary(items[cid], ex.normalized)
    fam: dict[str, dict[str, int]] = {}
    for cid, okay in results.items():
        f = fam.setdefault(items[cid]["family"], {"n": 0, "correct": 0})
        f["n"] += 1
        f["correct"] += okay
    n, k = len(results), sum(results.values())
    lo, hi = stats.wilson(k, n)
    return {
        "n": n,
        "correct": k,
        "accuracy": (k / n) if n else None,
        "ci95": [lo, hi] if n else None,
        "by_family": fam,
        "items": results,
        "unscored": skipped,
    }
