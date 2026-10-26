"""Input adapter for K2-Vendor-Verifier / Prism style corpora, so results stay comparable with Moonshot's verifier.

Corpus format (K2VV ``samples.jsonl``): one raw chat-completions request body per line (``messages``, ``tools``,
``temperature`` ...). The special role ``_input`` is rewritten to ``system``, as K2VV does. The reference
("official") behaviour comes from, in this order:

1. ``--reference results.jsonl``: a K2VV ``results.jsonl`` from the official API (``finish_reason`` per line,
   aligned by ``data_index`` or line order);
2. a ``reference`` object on the corpus line (``{"finish_reason": "tool_calls"}``), also accepted as
   ``expected_finish_reason``.

Scores: tool-call trigger F1 of ``finish_reason == "tool_calls"`` against the reference, and schema accuracy
(tool-call responses whose arguments validate / tool-call responses), as FLock's ``compare.py`` and Prism do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from glmkit.client import GLMClient
from glmkit.conform.checks.tools import request_tools, validate_call
from glmkit.conform.stats import f1_scores
from glmkit.contracts.evidence import Exchange
from glmkit.paths import default_case_roots

REQUEST_KEYS = ("messages", "tools", "tool_choice", "temperature", "top_p", "max_tokens", "stop")
IGNORED_KEYS = {"model", "stream", "stream_options", "reference", "expected_finish_reason", "data_index"}


class CorpusError(ValueError):
    pass


@dataclass
class Sample:
    index: int
    body: dict[str, Any]
    reference: str | None = None


def resolve_corpus(path: str | Path) -> Path:
    """An absolute or cwd-relative path, else relative to a case root (``cases/``) or its ``fixtures/`` folder."""
    p = Path(path)
    candidates = [p] if p.is_absolute() else [Path.cwd() / p]
    for root in default_case_roots():
        candidates += [root / p, root / "fixtures" / p]
    for c in candidates:
        if c.is_file():
            return c
    raise CorpusError(f"corpus {str(path)!r} not found (looked in {[str(c) for c in candidates]})")


def load_reference(path: str | Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        rec = json.loads(line)
        fr = rec.get("finish_reason") or ((rec.get("response") or {}).get("choices") or [{}])[0].get("finish_reason")
        if fr:
            out[int(rec.get("data_index", i))] = fr
    return out


def load_corpus(path: str | Path, *, limit: int | None = None, reference: str | Path | None = None) -> list[Sample]:
    ref = load_reference(reference) if reference else {}
    samples: list[Sample] = []
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    for i, line in enumerate(lines[:limit]):
        try:
            body = json.loads(line)
        except ValueError as e:
            raise CorpusError(f"{path}:{i + 1}: not JSON ({e})") from e
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            raise CorpusError(f"{path}:{i + 1}: expected an object with a 'messages' list")
        for m in body["messages"]:
            if isinstance(m, dict) and m.get("role") == "_input":
                m["role"] = "system"
        r = body.get("reference")
        own = r.get("finish_reason") if isinstance(r, dict) else body.get("expected_finish_reason")
        samples.append(Sample(index=i, body=body, reference=ref.get(int(body.get("data_index", i))) or own))
    return samples


def canonical(body: dict[str, Any], *, max_tokens: int) -> dict[str, Any]:
    """A K2VV request body as a canonical request (the client re-translates it for the target's protocol)."""
    canon = {k: body[k] for k in REQUEST_KEYS if body.get(k) is not None}
    canon.setdefault("max_tokens", max_tokens)
    extra = {k: v for k, v in body.items() if k not in REQUEST_KEYS and k not in IGNORED_KEYS}
    if extra:
        canon["params"] = extra
    return canon


async def replay(
    client: GLMClient, samples: list[Sample], *, stream: bool = False, max_tokens: int = 512
) -> list[Exchange]:
    """Send every sample; the reference finish reason travels in the exchange label (``k2vv:<i>:ref=<finish>``)."""
    out = []
    for s in samples:
        label = f"k2vv:{s.index}" + (f":ref={s.reference}" if s.reference else "")
        out.append(await client.send_canonical(canonical(s.body, max_tokens=max_tokens), stream=stream, label=label))
    return out


def reference_of(ex: Exchange) -> str | None:
    return ex.label.split("ref=", 1)[1] if ex.label.startswith("k2vv:") and "ref=" in ex.label else None


def score(exchanges: list[Exchange]) -> dict[str, Any] | None:
    """Trigger confusion matrix, precision / recall / F1 and schema accuracy; None when nothing has a reference."""
    tp = fp = fn = tn = called = valid = scored = errors = 0
    for ex in exchanges:
        ref = reference_of(ex)
        if ref is None:
            continue
        scored += 1
        if ex.response is None or not ex.normalized.ok:
            errors += 1
        got, want = ex.normalized.finish_reason_norm == "tool_calls", ref == "tool_calls"
        tp, fp = tp + (got and want), fp + (got and not want)
        fn, tn = fn + (want and not got), tn + (not got and not want)
        if got:
            called += 1
            tools = request_tools(ex)
            valid += all(validate_call(c, tools)["error_class"] is None for c in ex.normalized.tool_calls)
    if not scored:
        return None
    precision, recall, f1 = f1_scores(tp, fp, fn)
    return {
        "n": scored,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tool_call_responses": called,
        "schema_accuracy": round(valid / called, 4) if called else 0.0,
        "request_errors": errors,
    }
