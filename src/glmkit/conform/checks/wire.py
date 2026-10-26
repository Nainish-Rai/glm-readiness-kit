"""Wire-contract checks (slice 03): effort, model names, bad input, tool_choice, stop words, JSON mode, SSE
shape, size limits, Anthropic usage, auth, token accounting and rate limits.

Research: research/01-conformance.md §2 and §5 (WIRE-003 ... WIRE-018, ANTH-001 ... ANTH-005, AUTH-001, LIM-001).
All names start with ``wire_`` so they cannot collide with other slices. A check that needs a specific
exchange of a multi-request executor selects it with ``exchange: <label>``; the ones that look at several
exchanges say so in their description.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any

from glmkit.conform.checks import CheckContext, CheckOutcome, check, fail, ok, skip
from glmkit.conform.checks.http import INTERNAL_LEAK
from glmkit.contracts.evidence import Exchange
from glmkit.zai_errors import classify, retry_after_hint

TOKEN_REFERENCE_ENV = "GLMKIT_TOKEN_REFERENCE"


def _labelled(ctx: CheckContext, prefix: str) -> list[Exchange]:
    return [e for e in ctx.exchanges if (e.label or "").startswith(prefix)]


def _status(ex: Exchange) -> int | None:
    return ex.response.status if ex.response else None


def _no_response(ex: Exchange | None) -> CheckOutcome | None:
    if ex is None:
        return fail("no exchange recorded")
    if ex.response is None:
        return fail(
            f"no HTTP response ({ex.transport_error or 'unknown transport error'})", observed=ex.transport_error
        )
    return None


def _req_body(ex: Exchange) -> dict[str, Any]:
    return ex.request.body if isinstance(ex.request.body, dict) else {}


# ------------------------------------------------------------------ error taxonomy


@check("wire_error_code_known")
def wire_error_code_known(ctx: CheckContext) -> CheckOutcome:
    """An error response's business code is in the documented Z.ai table (glmkit.zai_errors); unknown codes fail."""
    ex = ctx.exchange
    if ex is None or ex.response is None:
        return skip("no response")
    n = ex.normalized
    if n.http_status is None or n.http_status < 400:
        return skip("response is not an error")
    info = classify(n.error_code, http_status=n.http_status, message=n.error_message)
    if info.known:
        return ok(f"{n.error_code or '(none)'} -> {info.category}", observed=info.to_dict())
    return fail(
        f"code {n.error_code!r} is not in the documented table (HTTP {n.http_status})",
        expected="a documented code",
        observed=n.error_code,
    )


# ------------------------------------------------------------------ WIRE-003 .. WIRE-006


@check("wire_effort_monotone")
def wire_effort_monotone(ctx: CheckContext) -> CheckOutcome:
    """Median completion tokens grow with reasoning_effort (exchanges labelled ``effort:<level>#<run>``)."""
    levels = ctx.params.get("levels", ["low", "high", "max"])
    slack = float(ctx.params.get("slack", 0.1))
    med: dict[str, float] = {}
    for lvl in levels:
        exs = _labelled(ctx, f"effort:{lvl}#")
        bad = [e for e in exs if not e.normalized.ok or not e.normalized.usage]
        if not exs or bad:
            got = [(_status(e), e.normalized.error_code) for e in bad] or "no exchanges"
            return fail(f"effort {lvl!r} did not return a normal answer: {got}", observed=got)
        med[lvl] = statistics.median(e.normalized.usage.completion_tokens or 0 for e in exs)  # type: ignore[union-attr]
    seq = [med[lvl] for lvl in levels]
    shown = " <= ".join(f"{lvl}:{med[lvl]:g}" for lvl in levels)
    for a, b in itertools.pairwise(seq):
        if b < a * (1 - slack):
            return fail(f"completion tokens shrink as effort grows ({shown})", expected="non-decreasing", observed=med)
    if not seq[-1] > seq[0]:
        return fail(f"effort has no effect on thinking length ({shown})", expected="max > low", observed=med)
    return ok(shown, observed=med)


@check("wire_unknown_model_rejected")
def wire_unknown_model_rejected(ctx: CheckContext) -> CheckOutcome:
    """An unknown model is rejected with a 4xx; it must never be answered 200 (by another model)."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None
    n = ex.normalized
    if n.http_status is not None and n.http_status < 300:
        asked = _req_body(ex).get("model")
        return fail(
            f"unknown model {asked!r} was answered 200 (response model {n.model!r})",
            expected="4xx",
            observed={"asked": asked, "answered_by": n.model},
        )
    if n.http_status is not None and n.http_status >= 500:
        return fail(f"HTTP {n.http_status}", expected="4xx", observed=n.http_status)
    return ok(f"HTTP {n.http_status} code={n.error_code}", observed=n.http_status)


# ------------------------------------------------------------------ WIRE-007 .. WIRE-010


@check("wire_anthropic_error_shape")
def wire_anthropic_error_shape(ctx: CheckContext) -> CheckOutcome:
    """Anthropic-shaped error body: ``{type: "error", error: {type, message}}`` (``types`` limits error.type)."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None and ex.response is not None
    if (ex.response.status or 0) < 400:
        return skip("response is not an error")
    body = ex.response.body_json
    if not (isinstance(body, dict) and body.get("type") == "error" and isinstance(body.get("error"), dict)):
        return fail("not an Anthropic error body", expected='{"type":"error","error":{...}}', observed=body)
    err = body["error"]
    types = ctx.params.get("types")
    if not err.get("type") or not err.get("message"):
        return fail("error.type or error.message missing", observed=err)
    if types and err["type"] not in types:
        return fail(f"error.type {err['type']!r} not in {types}", expected=types, observed=err["type"])
    return ok(f"error.type={err['type']}", observed=err["type"])


@check("wire_large_body_handled")
def wire_large_body_handled(ctx: CheckContext) -> CheckOutcome:
    """A body over ~1 MB gets a 4xx (1261 / 413) or is fully processed: usage.prompt_tokens reflects its size."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None
    n = ex.normalized
    status = n.http_status or 0
    if 400 <= status < 500:
        return ok(f"rejected with HTTP {status} code={n.error_code}", observed=status)
    if status >= 500:
        return fail(f"HTTP {status}", expected="4xx or 200", observed=status)
    size = ex.request.body_bytes
    floor = size / float(ctx.params.get("max_bytes_per_token", 8))
    got = n.usage.prompt_tokens if n.usage else None
    if not got or got < floor:
        return fail(
            f"200 but usage.prompt_tokens={got} for a {size}-byte body (expected >= {floor:.0f})",
            expected=f">= {floor:.0f}",
            observed=got,
        )
    return ok(f"processed: {got} prompt tokens for {size} bytes", observed=got)


# ------------------------------------------------------------------ WIRE-011 .. WIRE-014


def _requested_tool_choice(ex: Exchange) -> tuple[str, str | None]:
    tc = _req_body(ex).get("tool_choice")
    if isinstance(tc, dict):
        t = tc.get("type")
        if t == "none":
            return "none", None
        if t == "any":
            return "required", None
        if t == "tool":
            return "function", tc.get("name")
        if t == "function":
            return "function", (tc.get("function") or {}).get("name")
        return "auto", None
    return {"none": "none", "required": "required"}.get(str(tc), "auto"), None


@check("wire_tool_choice_honored")
def wire_tool_choice_honored(ctx: CheckContext) -> CheckOutcome:
    """tool_choice none / required / named function: a rejection is recorded, an acceptance must be honoured."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None
    n = ex.normalized
    kind, name = _requested_tool_choice(ex)
    if kind == "auto":
        return skip("request did not set tool_choice")
    if n.http_status and n.http_status >= 500:
        return fail(f"HTTP {n.http_status}", expected="accept or 4xx", observed=n.http_status)
    if n.http_status and n.http_status >= 400:
        return ok(f"tool_choice {kind} rejected: HTTP {n.http_status} code={n.error_code} (recorded)")
    calls = [c.name for c in n.tool_calls]
    if kind == "none" and calls:
        return fail(f"tool_choice none but the model called {calls}", expected=[], observed=calls)
    if kind == "required" and not calls:
        return fail("tool_choice required but no tool was called", expected=">= 1 call", observed=calls)
    if kind == "function" and (not calls or any(c != name for c in calls)):
        return fail(f"tool_choice named {name!r} but calls were {calls}", expected=[name], observed=calls)
    return ok(f"tool_choice {kind}{' ' + name if name else ''} honoured: calls={calls}", observed=calls)


@check("wire_stop_honoured")
def wire_stop_honoured(ctx: CheckContext) -> CheckOutcome:
    """Stop words are honoured (none appears in the answer), or rejected with 400/121x (recorded)."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None
    n = ex.normalized
    body = _req_body(ex)
    stops = body.get("stop") or body.get("stop_sequences") or []
    stops = [stops] if isinstance(stops, str) else list(stops)
    if (n.http_status or 0) >= 500:
        return fail(f"HTTP {n.http_status}", expected="200 or 400", observed=n.http_status)
    if (n.http_status or 0) >= 400:
        if str(n.error_code) in ("1210", "1214"):
            return ok(f"{len(stops)} stop word(s) rejected: HTTP {n.http_status} code={n.error_code} (recorded)")
        return fail(f"rejected with HTTP {n.http_status} code={n.error_code}", expected="1214", observed=n.error_code)
    hit = [s for s in stops if s in (n.content or "")]
    if hit:
        return fail(f"stop word(s) {hit} still in the answer", expected="stopped before the word", observed=n.content)
    return ok(f"{len(stops)} stop word(s) honoured", observed=n.content)


@check("wire_json_parseable")
def wire_json_parseable(ctx: CheckContext) -> CheckOutcome:
    """JSON mode: the content parses as a JSON object as received (no code fence, no prose)."""
    n = ctx.response
    if not n.content:
        return fail("empty content", expected="a JSON object", observed=n.content)
    try:
        value = json.loads(n.content)
    except ValueError as e:
        return fail(f"content is not JSON: {e}", expected="a JSON object", observed=n.content[:200])
    if not isinstance(value, dict):
        return fail(f"JSON is a {type(value).__name__}, not an object", observed=n.content[:200])
    return ok(f"{len(value)} key(s)", observed=list(value))


@check("wire_outputs_identical", aggregate=True)
def wire_outputs_identical(ctx: CheckContext) -> CheckOutcome:
    """Every non-throttled attempt returned the same answer text (do_sample false)."""
    scored = [a for a in ctx.attempts if a.status != "throttled" and a.exchanges]
    if not scored:
        return skip("no attempt to compare")
    outs = []
    for a in scored:
        n = a.exchanges[-1].normalized
        if not n.ok:
            return fail(f"attempt {a.index}: HTTP {n.http_status} code={n.error_code}", observed=n.http_status)
        outs.append(n.content)
    distinct = list(dict.fromkeys(outs))
    if len(distinct) > 1:
        return fail(f"{len(distinct)} different answers in {len(outs)} runs", expected=1, observed=distinct[:3])
    return ok(f"{len(outs)} runs, one answer", observed=distinct[0])


# ------------------------------------------------------------------ WIRE-015 .. WIRE-017 (streams)


def _events(ex: Exchange | None) -> list[Any] | None:
    return ex.response.events if ex and ex.response else None


@check("wire_stream_role_once")
def wire_stream_role_once(ctx: CheckContext) -> CheckOutcome:
    """The assistant role is announced exactly once in a stream (delta.role / message_start)."""
    ex = ctx.exchange
    if ex is None or ex.mode != "stream" or ctx.response.stream is None:
        return skip("not a streamed response")
    roles = ctx.response.stream.role_events
    if roles != 1:
        return fail(f"{roles} role announcements", expected=1, observed=roles)
    return ok("role announced once", observed=1)


@check("wire_stream_usage_final")
def wire_stream_usage_final(ctx: CheckContext) -> CheckOutcome:
    """Usage with completion tokens arrives with the last data chunk (OpenAI) or the last message_delta (Anthropic)."""
    ex = ctx.exchange
    evs = _events(ex)
    if ex is None or ex.mode != "stream" or evs is None:
        return skip("not a streamed response")
    if ex.protocol == "anthropic_messages":
        deltas = [e for e in evs if isinstance(e.data, dict) and e.data.get("type") == "message_delta"]
        usage = (deltas[-1].data.get("usage") or {}) if deltas else {}
        where = "last message_delta"
    else:
        data = [e for e in evs if isinstance(e.data, dict)]
        usage = (data[-1].data.get("usage") or {}) if data else {}
        where = "last data chunk"
    tokens = usage.get("completion_tokens") or usage.get("output_tokens")
    if not tokens:
        return fail(f"no usage with completion tokens on the {where}", expected="usage", observed=usage or None)
    return ok(f"{where} carries usage ({tokens} completion tokens)", observed=usage)


def _kinds(ex: Exchange) -> list[str]:
    """'r' for a reasoning delta, 'c' for an answer-text delta, in stream order."""
    out: list[str] = []
    for e in _events(ex) or []:
        d = e.data
        if not isinstance(d, dict):
            continue
        if ex.protocol == "anthropic_messages":
            t = (d.get("delta") or {}).get("type")
            if t == "thinking_delta" and (d["delta"].get("thinking") or ""):
                out.append("r")
            elif t == "text_delta" and (d["delta"].get("text") or ""):
                out.append("c")
        else:
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("reasoning_content") or delta.get("reasoning"):
                    out.append("r")
                if delta.get("content"):
                    out.append("c")
    return out


@check("wire_reasoning_before_content")
def wire_reasoning_before_content(ctx: CheckContext) -> CheckOutcome:
    """In a stream all reasoning deltas precede the first answer-text delta (none arrive after it)."""
    ex = ctx.exchange
    if ex is None or ex.mode != "stream" or _events(ex) is None:
        return skip("not a streamed response")
    kinds = _kinds(ex)
    if "r" not in kinds:
        return fail("no reasoning deltas in the stream", expected="reasoning first", observed="".join(kinds))
    if "c" not in kinds:
        return fail("no answer-text deltas in the stream", observed="".join(kinds))
    first_c = kinds.index("c")
    late = kinds[first_c:].count("r")
    if late:
        return fail(f"{late} reasoning delta(s) after the first answer text", expected="r*c*", observed=_runs(kinds))
    return ok(f"{kinds.count('r')} reasoning then {kinds.count('c')} text deltas", observed=_runs(kinds))


def _runs(kinds: list[str]) -> str:
    out: list[str] = []
    for k in kinds:
        if not out or out[-1][0] != k:
            out.append(k)
    return ">".join({"r": "reasoning", "c": "content"}[k[0]] for k in out)


@check("wire_stream_agrees_with_nonstream")
def wire_stream_agrees_with_nonstream(ctx: CheckContext) -> CheckOutcome:
    """The streamed answer and reasoning, reassembled, are in the same length class as the non-stream ones
    (exchanges labelled ``nonstream`` and ``stream``; ``max_ratio`` default 5)."""
    by = {e.label: e for e in ctx.exchanges}
    a, b = by.get("nonstream"), by.get("stream")
    if a is None or b is None:
        return fail("needs a nonstream and a stream exchange", observed=sorted(by))
    max_ratio = float(ctx.params.get("max_ratio", 5))
    for part in ("content", "reasoning"):
        la, lb = len(getattr(a.normalized, part) or ""), len(getattr(b.normalized, part) or "")
        if (la == 0) != (lb == 0):
            return fail(
                f"{part}: {la} chars non-stream vs {lb} chars streamed", observed={"nonstream": la, "stream": lb}
            )
        if la and not (1 / max_ratio <= lb / la <= max_ratio):
            return fail(
                f"{part}: {la} vs {lb} chars differ more than {max_ratio}x", observed={"nonstream": la, "stream": lb}
            )
    return ok(
        "lengths agree",
        observed={"content": len(b.normalized.content or ""), "reasoning": len(b.normalized.reasoning or "")},
    )


@check("wire_context_overflow_handled")
def wire_context_overflow_handled(ctx: CheckContext) -> CheckOutcome:
    """A prompt past the context window ends as finish_reason model_context_window_exceeded, or 400/1261 (or 413)."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None
    n = ex.normalized
    status = n.http_status or 0
    if status >= 500:
        return fail(f"HTTP {status}", expected="400/1261", observed=status)
    if status == 413:
        return ok("rejected with HTTP 413")
    if status >= 400:
        codes = [str(c) for c in ctx.params.get("codes", ["1261"])]
        if str(n.error_code) in codes:
            return ok(f"HTTP {status} code={n.error_code}", observed=n.error_code)
        return fail(f"HTTP {status} code={n.error_code}, expected {codes}", expected=codes, observed=n.error_code)
    if n.finish_reason == "model_context_window_exceeded":
        return ok("finish_reason model_context_window_exceeded")
    return fail(
        f"200 with finish_reason {n.finish_reason!r} for an over-long prompt",
        expected="model_context_window_exceeded or 400/1261",
        observed=n.finish_reason,
    )


# ------------------------------------------------------------------ ANTH-001 .. ANTH-005


@check("wire_message_start_input_tokens")
def wire_message_start_input_tokens(ctx: CheckContext) -> CheckOutcome:
    """Anthropic stream: message_start.usage reports input tokens > 0 (input + cache reads + cache writes)."""
    ex = ctx.exchange
    if ex is None or ex.mode != "stream" or ctx.response.stream is None:
        return skip("not a streamed response")
    u = ctx.response.stream.first_usage
    if not isinstance(u, dict):
        return fail("no usage in message_start", expected="usage.input_tokens > 0", observed=u)
    total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if total <= 0:
        return fail("message_start.usage input tokens are 0", expected="> 0", observed=u)
    return ok(f"{total} input tokens in message_start", observed=u)


@check("wire_thinking_signature")
def wire_thinking_signature(ctx: CheckContext) -> CheckOutcome:
    """Thinking blocks carry a non-empty ``signature`` (needed to send them back in the next turn)."""
    ex = ctx.exchange
    if ex is None or ex.response is None:
        return fail("no response")
    sigs: list[str] = []
    seen = False
    body = ex.response.body_json
    if isinstance(body, dict):
        for b in body.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "thinking":
                seen = True
                sigs.append(b.get("signature") or "")
    for e in ex.response.events or []:
        d = e.data
        if isinstance(d, dict) and isinstance(d.get("delta"), dict):
            if d["delta"].get("type") == "thinking_delta":
                seen = True
            if d["delta"].get("type") == "signature_delta":
                sigs.append(d["delta"].get("signature") or "")
    if not seen:
        return fail("no thinking block in the response", expected="thinking with signature")
    if not any(sigs):
        return fail("thinking blocks have no signature", expected="non-empty signature", observed=sigs)
    return ok(f"{sum(1 for s in sigs if s)} signature(s)")


@check("wire_tool_roundtrip")
def wire_tool_roundtrip(ctx: CheckContext) -> CheckOutcome:
    """Two-turn tool loop: turn1 ends in a tool call; turn2 (history incl. the thinking block) is answered 200."""
    by = {e.label: e for e in ctx.exchanges}
    t1, t2 = by.get("turn1"), by.get("turn2")
    if t1 is None or not t1.normalized.tool_calls:
        return fail("turn1 did not produce a tool call", observed=t1.normalized.finish_reason if t1 else None)
    if t2 is None:
        return fail("turn2 was not sent")
    n = t2.normalized
    if not n.ok:
        return fail(
            f"turn2 failed: HTTP {n.http_status} code={n.error_code} {n.error_message or ''}", observed=n.http_status
        )
    return ok(f"turn2 answered ({n.finish_reason_norm})")


@check("wire_alias_model_reported")
def wire_alias_model_reported(ctx: CheckContext) -> CheckOutcome:
    """A model alias is rejected (4xx, recorded) or served by a GLM model that the reply names (``glm-...``)."""
    ex = ctx.exchange
    bad = _no_response(ex)
    if bad:
        return bad
    assert ex is not None
    n = ex.normalized
    asked = _req_body(ex).get("model")
    if (n.http_status or 0) >= 500:
        return fail(f"HTTP {n.http_status}", observed=n.http_status)
    if (n.http_status or 0) >= 400:
        return ok(f"alias {asked!r} rejected: HTTP {n.http_status} code={n.error_code} (recorded)")
    if not n.model or not re.match(r"(?i)^(z-ai/)?glm-", n.model):
        return fail(
            f"alias {asked!r} answered 200 but the reply model is {n.model!r}, not a GLM model",
            expected="glm-*",
            observed={"asked": asked, "reply_model": n.model},
        )
    return ok(f"alias {asked!r} served by {n.model!r}", observed=n.model)


def _reference_file() -> dict[str, float]:
    path = os.environ.get(TOKEN_REFERENCE_ENV)
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}


@check("wire_token_ratio")
def wire_token_ratio(ctx: CheckContext) -> CheckOutcome:
    """Reported prompt tokens / reference tokens per corpus stay within ``max_ratio`` (default 1.3). The reference
    is the count_tokens answer (``ref:<corpus>`` exchanges) or the file named by $GLMKIT_TOKEN_REFERENCE."""
    max_ratio = float(ctx.params.get("max_ratio", 1.3))
    refs_file = _reference_file()
    rows: list[dict[str, Any]] = []
    for ex in _labelled(ctx, "corpus:"):
        name = ex.label.split(":", 1)[1]
        u = ex.normalized.usage
        tokens = u.prompt_tokens if u else None
        ref_ex = next((e for e in ctx.exchanges if e.label == f"ref:{name}"), None)
        ref = None
        if ref_ex and isinstance(ref_ex.response.body_json if ref_ex.response else None, dict):
            ref = ref_ex.response.body_json.get("input_tokens")  # type: ignore[union-attr]
        ref = ref if isinstance(ref, int | float) and ref > 0 else refs_file.get(name)
        rows.append(
            {
                "corpus": name,
                "bytes": ex.request.body_bytes,
                "http_status": ex.normalized.http_status,
                "tokens": tokens,
                "reference": ref,
                "ratio": round(tokens / ref, 3) if tokens and ref else None,
            }
        )
    if not rows:
        return fail("no corpus exchanges")
    broken = [r["corpus"] for r in rows if r["http_status"] != 200 or not r["tokens"]]
    if broken:
        return fail(f"no usable usage for corpora {broken}", expected="200 with usage", observed=rows)
    measured = [r for r in rows if r["ratio"] is not None]
    if not measured:
        return skip(f"no reference token counts (count_tokens unavailable and ${TOKEN_REFERENCE_ENV} not set)")
    over = [r for r in measured if r["ratio"] > max_ratio]
    worst = max(measured, key=lambda r: r["ratio"])
    if over:
        names = ", ".join(f"{r['corpus']} {r['ratio']:.2f}x" for r in over)
        return fail(f"over {max_ratio}x the reference: {names}", expected=f"<= {max_ratio}x", observed=rows)
    return ok(f"{len(measured)} corpora, worst {worst['corpus']} {worst['ratio']:.2f}x", observed=rows)


# ------------------------------------------------------------------ AUTH-001, LIM-001, multi-exchange sweeps


@check("wire_no_5xx_any")
def wire_no_5xx_any(ctx: CheckContext) -> CheckOutcome:
    """No exchange of the attempt was a 5xx or a transport failure (timeout, reset)."""
    if not ctx.exchanges:
        return fail("no exchange recorded")
    bad = [
        (e.label, _status(e) or e.transport_error)
        for e in ctx.exchanges
        if e.transport_error or (_status(e) or 0) >= 500
    ]
    if bad:
        return fail(
            f"{len(bad)} of {len(ctx.exchanges)} exchanges failed hard: {bad[:3]}", expected="< 500", observed=bad
        )
    return ok(f"{len(ctx.exchanges)} exchanges, none 5xx", observed=len(ctx.exchanges))


@check("wire_no_leak_any")
def wire_no_leak_any(ctx: CheckContext) -> CheckOutcome:
    """No exchange of the attempt leaks Python/Java exception text in its body."""
    for e in ctx.exchanges:
        text = (e.response.body_text or "") if e.response else ""
        for rx in INTERNAL_LEAK:
            m = rx.search(text)
            if m:
                return fail(f"{e.label}: internal error text leaked: {m.group(0)!r}", observed=text[:300])
    return ok(f"{len(ctx.exchanges)} exchanges clean")


@check("wire_cross_key_classified")
def wire_cross_key_classified(ctx: CheckContext) -> CheckOutcome:
    """Wrong-class key probes (``cross:*`` exchanges, e.g. a plan key on the standard URL) fail with a code that
    the taxonomy knows as auth / billing / permission. Skipped when no cross-class key was configured."""
    exs = _labelled(ctx, "cross:")
    if not exs:
        return skip("no cross-class key configured (see executor_config.probes[].key_env)")
    seen = []
    for e in exs:
        n = e.normalized
        info = classify(n.error_code, http_status=n.http_status, message=n.error_message)
        seen.append({"probe": e.label, "http": n.http_status, "code": n.error_code, "category": info.category})
        if (n.http_status or 0) >= 500 or info.category not in ("auth", "billing", "permission", "usage_limit"):
            return fail(f"{e.label}: HTTP {n.http_status} code={n.error_code} -> {info.category}", observed=seen)
    return ok(f"{len(exs)} cross-class probe(s) classified", observed=seen)


@check("wire_rate_limit_wellformed")
def wire_rate_limit_wellformed(ctx: CheckContext) -> CheckOutcome:
    """A burst ends only in 2xx or well-formed 429s (known throttle code, JSON error body, Retry-After hint)."""
    exs = ctx.exchanges
    if not exs:
        return fail("no exchange recorded")
    hard = [(e.label, _status(e) or e.transport_error) for e in exs if e.transport_error or (_status(e) or 0) >= 500]
    if hard:
        return fail(f"{len(hard)} of {len(exs)} burst requests failed hard: {hard[:3]}", observed=hard)
    throttled = [e for e in exs if _status(e) == 429]
    if not throttled:
        return ok(
            f"no throttling at a burst of {len(exs)} (limit not reached)", observed={"total": len(exs), "throttled": 0}
        )
    problems = []
    for e in throttled:
        n = e.normalized
        info = classify(
            n.error_code, http_status=429, message=n.error_message, headers=e.response.headers if e.response else None
        )
        if not info.known or info.category not in ("rate_limit", "overloaded", "usage_limit"):
            problems.append(f"{e.label}: code {n.error_code!r} is not a documented throttle code")
        elif info.retry_after_s is None:
            problems.append(f"{e.label}: no Retry-After header or reset time in the message")
        elif not (e.response and isinstance(e.response.body_json, dict)):
            problems.append(f"{e.label}: body is not a JSON error")
    stats = {"total": len(exs), "throttled": len(throttled), "share": round(len(throttled) / len(exs), 3)}
    if problems:
        return fail(
            "; ".join(problems[:3]),
            expected="429 + 1302/1305/13xx + Retry hint",
            observed=stats | {"problems": problems},
        )
    hint = retry_after_hint(
        throttled[0].normalized.error_message, throttled[0].response.headers if throttled[0].response else None
    )
    return ok(f"{len(throttled)} of {len(exs)} throttled, all well-formed (retry hint {hint:g}s)", observed=stats)
