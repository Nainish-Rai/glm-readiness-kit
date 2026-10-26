"""Checks for multi-turn agent loops (issue 04): reasoning replay, degeneracy, stream parity, caching, language,
usage and soak. Names are specific on purpose; they read exchanges by label (``turn0``, ``nonstream`` ...)."""

# ruff: noqa: E501  (check docstrings are the one-line help shown by `glmkit conform checks`)
from __future__ import annotations

import itertools
import json
import os
import re
from typing import Any

from glmkit.agentloop.metrics import (
    cjk_leaks,
    degeneracy_metrics,
    is_degenerate,
    language_of,
    wilson,
)
from glmkit.conform.checks import CheckContext, CheckOutcome, check, compare, fail, ok, skip
from glmkit.contracts.evidence import Exchange

TOOL_MARKUP = re.compile(r"</?tool_call>|</?arg_key>|</?arg_value>")


def _exchanges(ctx: CheckContext) -> list[Exchange]:
    """``scope: all`` = every exchange (optionally those whose label starts with ``prefix``), else the one named
    by ``exchange`` (default: the last)."""
    if ctx.params.get("scope") == "all":
        pre = ctx.params.get("prefix")
        return [e for e in ctx.exchanges if not pre or e.label.startswith(pre)]
    ex = ctx.exchange
    return [ex] if ex else []


def _labelled(ctx: CheckContext, label: str) -> Exchange | None:
    return next((e for e in ctx.exchanges if e.label == label), None)


def _good(exs: list[Exchange]) -> list[Exchange]:
    return [e for e in exs if e.normalized.ok]


def _texts(e: Exchange) -> list[str]:
    n = e.normalized
    return [t for t in (n.reasoning, n.content) if t]


def _usage(e: Exchange | None, field: str) -> int | None:
    u = e.normalized.usage if e else None
    return getattr(u, field, None) if u else None


# ------------------------------------------------------------------ requests and replay


@check("all_turns_ok")
def all_turns_ok(ctx: CheckContext) -> CheckOutcome:
    """Every exchange in the loop (optionally with label ``prefix``) answered 2xx without an error body."""
    pre = ctx.params.get("prefix")
    exs = [e for e in ctx.exchanges if not pre or e.label.startswith(pre)]
    if not exs:
        return fail("no exchanges recorded")
    bad = [e for e in exs if not e.normalized.ok]
    if bad:
        e = bad[0]
        return fail(
            f"{e.label}: HTTP {e.normalized.http_status} {e.normalized.error_code or ''} "
            f"{(e.normalized.error_message or e.transport_error or '')[:100]}".strip(),
            expected="2xx on every turn",
            observed=[(b.label, b.normalized.http_status) for b in bad],
        )
    return ok(f"{len(exs)} turns answered", observed=len(exs))


@check("agent_turn_has_tool_call")
def agent_turn_has_tool_call(ctx: CheckContext) -> CheckOutcome:
    """The named turn (``exchange``) returned at least one tool call, so the loop can continue."""
    n = ctx.response
    if n.tool_calls:
        return ok(f"{len(n.tool_calls)} tool call(s): {[t.name for t in n.tool_calls]}")
    return fail(
        "no tool call in the turn",
        expected="a tool call",
        observed={"finish_reason": n.finish_reason, "content": (n.content or "")[:120]},
    )


@check("no_tool_markup_in_text")
def no_tool_markup_in_text(ctx: CheckContext) -> CheckOutcome:
    """``<tool_call>`` / ``<arg_key>`` / ``<arg_value>`` markup must not appear in content or reasoning (opencode#6708)."""
    hits = []
    for e in _exchanges(ctx):
        for part, text in (("content", e.normalized.content), ("reasoning", e.normalized.reasoning)):
            if text and TOOL_MARKUP.search(text):
                hits.append({"exchange": e.label, "in": part, "text": text[:160]})
    if hits:
        return fail(f"tool-call markup leaked into {hits[0]['in']} ({hits[0]['exchange']})", observed=hits[:3])
    return ok("no tool-call markup in text or reasoning")


# ------------------------------------------------------------------ degeneracy


@check("reasoning_degeneracy")
def reasoning_degeneracy(ctx: CheckContext) -> CheckOutcome:
    """No degenerate reasoning/content: share of '='/'the' words or looped tokens above ``max`` (0.2), loops, U+FFFD."""
    limit = float(ctx.params.get("max", 0.2))
    exs = _good(_exchanges(ctx))
    if not exs:
        return skip("no successful response to score")
    worst: tuple[float, str, dict[str, Any]] = (-1.0, "", {})
    for e in exs:
        for part, text in (("reasoning", e.normalized.reasoning), ("content", e.normalized.content)):
            m = degeneracy_metrics(text)
            if is_degenerate(m, limit):
                return fail(
                    f"{e.label} {part} degenerate: score {m['score']} (= / the {m['eq_the_share']}, "
                    f"loops {m['repeated_token_share']}, char repeat {m['char_repeat']}, U+FFFD {m['fffd']})",
                    expected=f"score <= {limit}",
                    observed={"exchange": e.label, "part": part, **m},
                )
            if m["score"] > worst[0]:
                worst = (m["score"], e.label, m)
    return ok(f"worst degeneracy score {worst[0]} ({worst[1]})", expected=f"<= {limit}", observed=worst[2])


@check("no_replacement_chars")
def no_replacement_chars(ctx: CheckContext) -> CheckOutcome:
    """No U+FFFD (mojibake) in content or reasoning."""
    n = 0
    where = ""
    for e in _good(_exchanges(ctx)):
        for t in _texts(e):
            c = t.count("�")
            if c and not where:
                where = e.label
            n += c
    if n:
        return fail(f"{n} U+FFFD character(s), first in {where}", expected=0, observed=n)
    return ok("no U+FFFD")


@check("no_empty_length_stop")
def no_empty_length_stop(ctx: CheckContext) -> CheckOutcome:
    """A response that stops with finish_reason=length must still carry content or a tool call (vllm#56868)."""
    for e in _good(_exchanges(ctx)):
        n = e.normalized
        if n.finish_reason_norm == "length" and not (n.content or "").strip() and not n.tool_calls:
            return fail(
                f"{e.label}: finish_reason=length with empty content",
                expected="non-empty content",
                observed={"exchange": e.label, "reasoning_chars": len(n.reasoning or "")},
            )
    return ok("no empty content under finish_reason=length")


@check("soak_degeneracy_rate")
def soak_degeneracy_rate(ctx: CheckContext) -> CheckOutcome:
    """Degenerate turns per 100 turns across the loop must not exceed ``max_per_100`` (default 0)."""
    exs = _good([e for e in ctx.exchanges if e.label.startswith("turn")])
    if not exs:
        return skip("no successful turns")
    bad = [
        e.label
        for e in exs
        if any(is_degenerate(degeneracy_metrics(t), float(ctx.params.get("max", 0.2))) for t in _texts(e))
    ]
    rate = 100 * len(bad) / len(exs)
    cap = float(ctx.params.get("max_per_100", 0))
    msg = f"{len(bad)} of {len(exs)} turns degenerate ({rate:.1f} per 100)" + (f"; first {bad[0]}" if bad else "")
    if rate > cap:
        return fail(msg, expected=f"<= {cap} per 100", observed={"rate_per_100": rate, "turns": bad[:10]})
    return ok(msg, expected=f"<= {cap} per 100", observed={"rate_per_100": rate})


@check("soak_budget")
def soak_budget(ctx: CheckContext) -> CheckOutcome:
    """The soak stopped within its token budget (decoded tokens may exceed it by at most one turn)."""
    env = os.environ.get("GLMKIT_SOAK_BUDGET_TOKENS")
    budget = int(env or ctx.case.executor_config.get("budget_tokens", 200_000))
    decoded = [(_usage(e, "completion_tokens") or 0) for e in ctx.exchanges]
    total = sum(decoded)
    turns = len(decoded)
    slack = max(decoded, default=0)
    msg = f"{turns} turns, {total} decoded tokens (budget {budget})"
    if total > budget + slack:
        return fail(msg + ": budget exceeded", expected=f"<= {budget}", observed=total)
    return ok(msg, expected=f"<= {budget}", observed=total)


# ------------------------------------------------------------------ caching and usage


@check("cache_usage_field_present")
def cache_usage_field_present(ctx: CheckContext) -> CheckOutcome:
    """``usage.prompt_tokens_details.cached_tokens`` exists in the response (the field is reported)."""
    c = _usage(ctx.exchange, "cached_tokens")
    if c is None:
        return fail("cached_tokens missing from usage", expected="an integer", observed=None)
    return ok(f"cached_tokens={c}", observed=c)


@check("cached_tokens_positive")
def cached_tokens_positive(ctx: CheckContext) -> CheckOutcome:
    """The named exchange reports ``cached_tokens`` > 0 (a prefix cache hit)."""
    c = _usage(ctx.exchange, "cached_tokens")
    if not c:
        return fail(f"no cache hit (cached_tokens={c})", expected="> 0", observed=c)
    p = _usage(ctx.exchange, "prompt_tokens")
    return ok(f"cached_tokens={c} of {p} prompt tokens", observed=c)


@check("cached_tokens_grow")
def cached_tokens_grow(ctx: CheckContext) -> CheckOutcome:
    """Across the turns, cached_tokens never falls and the last turn's cached share is >= ``min_share`` (0.7)."""
    turns = [e for e in ctx.exchanges if e.label.startswith("turn") and e.normalized.ok]
    if len(turns) < 2:
        return fail(f"only {len(turns)} successful turn(s)", expected=">= 2 turns")
    cached = [_usage(e, "cached_tokens") for e in turns]
    if any(c is None for c in cached):
        return fail("cached_tokens missing on some turns", observed=cached)
    series: list[int] = [int(c) for c in cached if c is not None]
    if any(b < a for a, b in itertools.pairwise(series)):
        return fail(f"cached_tokens fell during the loop: {series}", expected="non-decreasing", observed=series)
    last_p = _usage(turns[-1], "prompt_tokens") or 0
    share = series[-1] / last_p if last_p else 0.0
    need = float(ctx.params.get("min_share", 0.7))
    if series[-1] == 0:
        return fail(f"no cache hits in {len(series)} turns: {series}", expected="> 0", observed=series)
    if share < need:
        return fail(
            f"cached share on the last turn is {share:.0%} (< {need:.0%}); cached {series}",
            expected=f">= {need}",
            observed=round(share, 3),
        )
    return ok(f"cached {series}; last-turn share {share:.0%}", observed=series)


@check("prompt_tokens_lower")
def prompt_tokens_lower(ctx: CheckContext) -> CheckOutcome:
    """prompt_tokens of exchange ``a`` is lower than of exchange ``b`` (clear_thinking:true ignores replayed reasoning)."""
    a, b = _labelled(ctx, ctx.params["a"]), _labelled(ctx, ctx.params["b"])
    pa, pb = _usage(a, "prompt_tokens"), _usage(b, "prompt_tokens")
    if pa is None or pb is None:
        return fail("prompt_tokens missing", observed={"a": pa, "b": pb})
    if pa < pb:
        return ok(f"{ctx.params['a']}={pa} < {ctx.params['b']}={pb}", observed={"a": pa, "b": pb})
    return fail(
        f"{ctx.params['a']}={pa} is not lower than {ctx.params['b']}={pb}",
        expected="a < b",
        observed={"a": pa, "b": pb},
    )


@check("cached_tokens_not_above")
def cached_tokens_not_above(ctx: CheckContext) -> CheckOutcome:
    """cached_tokens of exchange ``a`` (edited replay) is not above exchange ``b`` (exact replay)."""
    a, b = _labelled(ctx, ctx.params["a"]), _labelled(ctx, ctx.params["b"])
    ca, cb = _usage(a, "cached_tokens"), _usage(b, "cached_tokens")
    if ca is None or cb is None:
        return fail("cached_tokens missing", observed={"a": ca, "b": cb})
    if ca <= cb:
        return ok(f"{ctx.params['a']} cached {ca} <= {ctx.params['b']} cached {cb}", observed={"a": ca, "b": cb})
    return fail(f"{ctx.params['a']} cached {ca} > {ctx.params['b']} cached {cb}", observed={"a": ca, "b": cb})


@check("usage_parity")
def usage_parity(ctx: CheckContext) -> CheckOutcome:
    """Exchanges ``a`` and ``b`` (default nonstream / stream) report the same prompt, completion and cached tokens within ``tolerance`` (1%)."""
    la, lb = ctx.params.get("a", "nonstream"), ctx.params.get("b", "stream")
    a, b = _labelled(ctx, la), _labelled(ctx, lb)
    tol = float(ctx.params.get("tolerance", 0.01))
    if a is None or b is None:
        return fail("missing exchange for usage comparison")
    rows = {}
    bad = []
    for f in ("prompt_tokens", "completion_tokens", "cached_tokens"):
        x, y = _usage(a, f), _usage(b, f)
        rows[f] = [x, y]
        if x is None or y is None:
            bad.append(f"{f} missing ({x} vs {y})")
            continue
        base = _usage(a, "prompt_tokens") if f == "cached_tokens" else x
        if abs(x - y) > tol * (base or 0):
            bad.append(f"{f} {x} vs {y}")
    if bad:
        return fail("usage differs between modes: " + "; ".join(bad), expected=f"within {tol:.0%}", observed=rows)
    return ok(f"usage equal within {tol:.0%}: {rows}", observed=rows)


# ------------------------------------------------------------------ stream parity


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def parity_diffs(ref: Exchange, other: Exchange, *, content: bool = True) -> list[str]:
    """Differences between a non-stream reference and a stream result: tool names, argument JSON (type-exact, so
    ``123`` != ``"123"``), finish reason, and optionally content and reasoning."""
    r, o = ref.normalized, other.normalized
    diffs = []
    if [t.name for t in r.tool_calls] != [t.name for t in o.tool_calls]:
        diffs.append(f"tool names {[t.name for t in r.tool_calls]} vs {[t.name for t in o.tool_calls]}")
    else:
        for a, b in zip(r.tool_calls, o.tool_calls, strict=True):
            if a.parse_error or b.parse_error:
                diffs.append(f"{a.name}: unparseable arguments ({a.parse_error or b.parse_error})")
            elif _canon(a.arguments_parsed) != _canon(b.arguments_parsed):
                diffs.append(f"{a.name} arguments {_canon(a.arguments_parsed)} vs {_canon(b.arguments_parsed)}")
    if r.finish_reason_norm != o.finish_reason_norm:
        diffs.append(f"finish_reason {r.finish_reason_norm} vs {o.finish_reason_norm}")
    if content and (r.content or "") != (o.content or ""):
        diffs.append("content differs")
    if content and (r.reasoning or "") != (o.reasoning or ""):
        diffs.append("reasoning differs")
    return diffs


@check("stream_nonstream_parity")
def stream_nonstream_parity(ctx: CheckContext) -> CheckOutcome:
    """Streamed and non-streamed answers to the same request agree on tool names, argument JSON (type-exact) and finish reason (``content: true`` also compares text)."""
    a, b = _labelled(ctx, ctx.params.get("a", "nonstream")), _labelled(ctx, ctx.params.get("b", "stream"))
    if a is None or b is None:
        return fail("missing exchange for parity comparison")
    for e in (a, b):
        if not e.normalized.ok:
            return fail(f"{e.label}: HTTP {e.normalized.http_status}", observed=e.normalized.error_message)
    diffs = parity_diffs(a, b, content=bool(ctx.params.get("content", False)))
    if diffs:
        return fail("stream differs from non-stream: " + "; ".join(diffs), observed=diffs)
    return ok(f"parity: {[t.name for t in a.normalized.tool_calls]} finish {a.normalized.finish_reason_norm}")


@check("stream_split_independent")
def stream_split_independent(ctx: CheckContext) -> CheckOutcome:
    """Every offline reassembly (1-char, 1-token, odd splits, interleaved indices) equals the non-stream reference. ``expect_divergence: true`` inverts it, as a detector self-test on a known-buggy fixture."""
    ref = _labelled(ctx, "nonstream")
    splits = [e for e in ctx.exchanges if e.label.startswith("split-")]
    if ref is None or not splits:
        return fail("fixture exchanges missing")
    diverging = {e.label: parity_diffs(ref, e) for e in splits}
    diverging = {k: v for k, v in diverging.items() if v}
    if ctx.params.get("expect_divergence"):
        if diverging:
            first = next(iter(diverging.values()))
            return ok(f"detector fired on {len(diverging)}/{len(splits)} splits: {first[0]}", observed=first)
        return fail("known-buggy fixture was not detected", expected="divergence", observed=[])
    if diverging:
        k, v = next(iter(diverging.items()))
        return fail(f"{len(diverging)}/{len(splits)} splits differ; {k}: {v[0]}", observed=v)
    return ok(f"{len(splits)} splits and orders reassemble identically to the non-stream result")


@check("tool_stream_fragments")
def tool_stream_fragments(ctx: CheckContext) -> CheckOutcome:
    """With tool_stream, each tool call's arguments arrive in >= ``min_fragments`` (2) fragments, every fragment carries its ``index``, and the concatenation parses and equals the non-stream arguments."""
    ex = _labelled(ctx, ctx.params.get("exchange", "stream"))
    if ex is None or ex.response is None or not ex.response.events:
        return fail("no streamed events recorded")
    counts: dict[int, int] = {}
    missing_index = 0
    for ev in ex.response.events:
        d = ev.data
        if not isinstance(d, dict):
            continue
        if ex.protocol == "anthropic_messages":
            delta = d.get("delta") or {}
            if delta.get("type") == "input_json_delta":
                counts[d.get("index", 0)] = counts.get(d.get("index", 0), 0) + 1
            continue
        for ch in d.get("choices") or []:
            for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                frag = (tc.get("function") or {}).get("arguments")
                if frag:
                    if "index" not in tc:
                        missing_index += 1
                    counts[tc.get("index", 0)] = counts.get(tc.get("index", 0), 0) + 1
    if not counts:
        return fail("no tool-call argument fragments in the stream")
    need = int(ctx.params.get("min_fragments", 2))
    if missing_index:
        return fail(f"{missing_index} fragment(s) without an index", expected="index on every fragment")
    few = {i: c for i, c in counts.items() if c < need}
    if few:
        return fail(f"tool call(s) {few} streamed in fewer than {need} fragments", observed=counts)
    ref = _labelled(ctx, ctx.params.get("compare", "nonstream"))
    if ref is not None:
        diffs = parity_diffs(ref, ex, content=False)
        if diffs:
            return fail("reassembled arguments differ from non-stream: " + "; ".join(diffs), observed=diffs)
    bad = [t.name for t in ex.normalized.tool_calls if t.parse_error]
    if bad:
        return fail(f"concatenated arguments do not parse for {bad}")
    return ok(f"fragments per index: {counts}; reassembly parses and equals non-stream", observed=counts)


# ------------------------------------------------------------------ language


def _inputs(e: Exchange) -> list[str]:
    """Text the model was given: system, user and tool messages of the request (never earlier assistant output)."""
    body = e.request.body if isinstance(e.request.body, dict) else {}
    out = []
    for m in body.get("messages") or []:
        if m.get("role") in ("system", "developer", "user", "tool"):
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            if c:
                out.append(str(c))
    if body.get("system"):
        out.append(str(body["system"]))
    return out


@check("no_cjk_leak")
def no_cjk_leak(ctx: CheckContext) -> CheckOutcome:
    """CJK characters in content or reasoning that are not quoted from the conversation's inputs. Fails above ``max_leaking_turns`` (0); reports the rate per 1000 turns with a Wilson interval, text and reasoning separately."""
    exs = _good(_exchanges(ctx))
    if not exs:
        return skip("no successful turns")
    leaks: list[dict[str, Any]] = []
    in_text = in_reason = 0
    for e in exs:
        t = cjk_leaks(e.normalized.content, _inputs(e))
        r = cjk_leaks(e.normalized.reasoning, _inputs(e))
        in_text += bool(t)
        in_reason += bool(r)
        if t or r:
            leaks.append({"exchange": e.label, "content": t[:3], "reasoning": r[:3]})
    n, k = len(exs), len(leaks)
    lo, hi = wilson(k, n)
    allowed = int(ctx.params.get("max_leaking_turns", 0))
    msg = (
        f"{k} of {n} turns leak CJK ({in_text} in text, {in_reason} in reasoning); "
        f"{1000 * k / n:.0f} per 1000 turns, 95% CI {1000 * lo:.0f}-{1000 * hi:.0f}"
    )
    if k > allowed:
        return fail(msg, expected=f"<= {allowed} turns", observed=leaks[:5])
    return ok(msg, expected=f"<= {allowed} turns", observed={"turns": n, "leaks": k})


@check("output_language_matches")
def output_language_matches(ctx: CheckContext) -> CheckOutcome:
    """The language of the answer text equals the language of the conversation inputs (or ``language``), for every turn with at least ``min_chars`` (4) of content."""
    exs = [
        e
        for e in _good(_exchanges(ctx))
        if len((e.normalized.content or "").strip()) >= int(ctx.params.get("min_chars", 4))
    ]
    if not exs:
        return skip("no answer text to judge")
    bad = []
    for e in exs:
        want = ctx.params.get("language") or language_of(" ".join(_inputs(e)))
        got = language_of(e.normalized.content)
        if got != want:
            bad.append({"exchange": e.label, "expected": want, "got": got, "text": (e.normalized.content or "")[:80]})
    if bad:
        return fail(
            f"{len(bad)} of {len(exs)} answers are not in the context language ({bad[0]['expected']} expected, "
            f"{bad[0]['got']} in {bad[0]['exchange']})",
            expected=bad[0]["expected"],
            observed=bad[:5],
        )
    return ok(f"{len(exs)} answers match the context language")


# ------------------------------------------------------------------ determinism, effort, golden usage


def _signature(e: Exchange) -> str:
    n = e.normalized
    return _canon([n.content, n.reasoning, [(t.name, t.arguments) for t in n.tool_calls]])


@check("identical_outputs")
def identical_outputs(ctx: CheckContext) -> CheckOutcome:
    """Repeated identical requests (temperature 0, seed) return the same text, reasoning and tool calls; exact-match rate >= ``min_rate`` (1.0)."""
    exs = _good(ctx.exchanges)
    if len(exs) < 2:
        return fail(f"only {len(exs)} successful run(s)", expected=">= 2")
    sigs = [_signature(e) for e in exs]
    top = max(sigs.count(s) for s in sigs)
    rate = top / len(sigs)
    lens = [len(s) for s in sigs]
    msg = f"exact-match rate {rate:.0%} over {len(sigs)} runs (output sizes {lens})"
    if rate < float(ctx.params.get("min_rate", 1.0)):
        return fail(msg, expected=f">= {ctx.params.get('min_rate', 1.0)}", observed=rate)
    return ok(msg, observed=rate)


@check("effort_clamp_observed")
def effort_clamp_observed(ctx: CheckContext) -> CheckOutcome:
    """Informational (self-hosted): documents whether reasoning_effort 'medium' behaves like 'max' (the chat template clamps it, vllm#56868). Fails only if the host rejects 'medium'."""
    med, mx = _labelled(ctx, "medium"), _labelled(ctx, "max")
    if med is None or not med.normalized.ok:
        return fail(f"'medium' was not accepted (HTTP {med.normalized.http_status if med else None})")
    if mx is None or not mx.normalized.ok:
        return skip("'max' reference missing")
    a, b = len(med.normalized.reasoning or ""), len(mx.normalized.reasoning or "")
    clamped = b > 0 and abs(a - b) <= 0.15 * b
    return ok(
        f"medium reasoning {a} chars vs max {b} chars: "
        + ("clamped to max (template behaviour)" if clamped else "distinct from max"),
        observed={"medium": a, "max": b, "clamped": clamped},
    )


@check("prompt_tokens_golden")
def prompt_tokens_golden(ctx: CheckContext) -> CheckOutcome:
    """prompt_tokens equals the golden constant recorded for this provider (``golden: {provider: N}``). Skipped where no constant exists yet (never guessed)."""
    got = _usage(ctx.exchange, "prompt_tokens")
    golden = (ctx.params.get("golden") or {}).get(ctx.target.provider)
    if golden is None:
        return skip(f"no golden constant for provider {ctx.target.provider!r}; prompt_tokens={got}")
    if got is None:
        return fail("prompt_tokens missing")
    return compare(got, {"equals": golden})


@check("prompt_tokens_ratio")
def prompt_tokens_ratio(ctx: CheckContext) -> CheckOutcome:
    """Reports prompt_tokens against a chars/4 estimate of the request text (plugins#12: JSON ~1.43x, special characters ~2.4x). Fails only if usage is missing."""
    got = _usage(ctx.exchange, "prompt_tokens")
    ex = ctx.exchange
    if got is None or ex is None:
        return fail("prompt_tokens missing")
    body = ex.request.body if isinstance(ex.request.body, dict) else {}
    chars = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
    est = max(1, chars // 4)
    return ok(
        f"prompt_tokens {got} vs chars/4 estimate {est}: ratio {got / est:.2f}",
        observed={"tokens": got, "estimate": est},
    )
