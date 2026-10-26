"""Tool-call checks (research/01-conformance.md §3, §5, §6; cases TOOL-001 ... TOOL-015).

Error taxonomy follows OpenRouter's: ``InvalidJson`` (arguments do not parse), ``UnknownName`` (the name is not
one of the offered tools), ``SchemaMismatch`` (arguments do not satisfy the tool's JSON Schema). Schemas are
validated against Draft 2020-12 AND Draft 7, and a disagreement between the two is reported.
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft7Validator, Draft202012Validator
from jsonschema.exceptions import SchemaError

from glmkit.conform.checks import CheckContext, CheckOutcome, check, compare, fail, ok, skip
from glmkit.conform.stats import wilson_interval
from glmkit.contracts.evidence import Exchange, NormToolCall

INVALID_JSON, UNKNOWN_NAME, SCHEMA_MISMATCH = "InvalidJson", "UnknownName", "SchemaMismatch"
MARKERS = ("<tool_call>", "</tool_call>", "<arg_key>", "</arg_key>", "<arg_value>", "</arg_value>")
EXACT_MARK = "exactly these arguments:"


# ------------------------------------------------------------------ helpers


def request_tools(ex: Exchange) -> dict[str, dict[str, Any]]:
    """Tool name -> JSON Schema of its parameters, read back from the request body that was actually sent."""
    body = ex.request.body if isinstance(ex.request.body, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for t in body.get("tools") or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or t
        schema = fn.get("parameters") if "parameters" in fn else fn.get("input_schema")
        if fn.get("name"):
            out[fn["name"]] = schema if isinstance(schema, dict) else {}
    return out


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def prompt_arguments(ex: Exchange) -> list[tuple[str | None, dict[str, Any]]]:
    """``(tool name or None, arguments)`` pairs the last user message asks for with ``exactly these arguments:``."""
    body = ex.request.body if isinstance(ex.request.body, dict) else {}
    users = [m for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "user"]
    text = _text(users[-1].get("content")) if users else ""
    names = list(request_tools(ex))
    dec, out, pos = json.JSONDecoder(), [], 0
    while (i := text.find(EXACT_MARK, pos)) >= 0:
        start = i + len(EXACT_MARK)
        pos = start
        while start < len(text) and text[start].isspace():
            start += 1
        try:
            obj, end = dec.raw_decode(text, start)
        except ValueError:
            continue
        before = text[max(0, i - 120) : i]
        named = [n for n in names if n in before]
        out.append((max(named, key=before.rfind) if named else None, obj))
        pos = end
    return out


def _validators(schema: dict[str, Any]) -> dict[str, Draft202012Validator | Draft7Validator | None]:
    out: dict[str, Any] = {}
    for label, cls in (("2020-12", Draft202012Validator), ("draft7", Draft7Validator)):
        try:
            cls.check_schema(schema)
            out[label] = cls(schema)
        except SchemaError:
            out[label] = None  # uncompilable schema: counted as valid (OpenRouter's conservative rule)
    return out


def validate_call(call: NormToolCall, tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Classify one tool call. ``error_class`` is None when valid under every validator that could compile."""
    res: dict[str, Any] = {"name": call.name, "id": call.id, "error_class": None, "draft2020": None, "draft7": None}
    if call.name not in tools:
        res["error_class"] = UNKNOWN_NAME
        return res
    if call.parse_error:
        res["error_class"] = INVALID_JSON
        res["detail"] = call.parse_error
        return res
    args = call.arguments_parsed if call.arguments_parsed is not None else {}
    if not isinstance(args, dict):
        res["error_class"] = SCHEMA_MISMATCH
        res["detail"] = "arguments are not a JSON object"
        return res
    for label, v in _validators(tools[call.name]).items():
        key = "draft2020" if label == "2020-12" else "draft7"
        if v is None:
            res[key] = "uncompilable"
            continue
        errs = sorted(v.iter_errors(args), key=lambda e: list(e.path))
        res[key] = "valid" if not errs else "invalid"
        if errs:
            res.setdefault(
                "detail", f"{label}: {'/'.join(str(p) for p in errs[0].path) or '<root>'}: {errs[0].message}"[:300]
            )
    if "invalid" in (res["draft2020"], res["draft7"]):
        res["error_class"] = SCHEMA_MISMATCH
    return res


def _type_name(v: Any) -> str:
    return "null" if v is None else type(v).__name__


def strict_diff(want: Any, got: Any, path: str = "$") -> str | None:
    """First difference between two JSON values, comparing types strictly (True != 1, 1 != 1.0, None != 'None')."""
    if _type_name(want) != _type_name(got):
        return f"{path}: expected {_type_name(want)} {_short(want)}, got {_type_name(got)} {_short(got)}"
    if isinstance(want, dict):
        for k in want:
            if k not in got:
                return f"{path}.{k}: missing"
            if (d := strict_diff(want[k], got[k], f"{path}.{k}")) is not None:
                return d
        extra = set(got) - set(want)
        return f"{path}: unexpected keys {sorted(extra)}" if extra else None
    if isinstance(want, list):
        if len(want) != len(got):
            return f"{path}: expected {len(want)} items, got {len(got)}"
        for i, (a, b) in enumerate(zip(want, got, strict=True)):
            if (d := strict_diff(a, b, f"{path}[{i}]")) is not None:
                return d
        return None
    if want != got:
        if isinstance(want, str):
            n = next((i for i, (a, b) in enumerate(zip(want, got, strict=False)) if a != b), min(len(want), len(got)))
            return f"{path}: string differs at char {n} (expected {len(want)} chars, got {len(got)})"
        return f"{path}: expected {want!r}, got {got!r}"
    return None


def _short(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "..."


# ------------------------------------------------------------------ per-attempt checks


@check("tool_call_count")
def tool_call_count(ctx: CheckContext) -> CheckOutcome:
    """Number of tool calls compared with equals / gte / lte ...; optional ``names`` = the exact multiset of names."""
    calls = ctx.response.tool_calls
    p = {k: v for k, v in ctx.params.items() if k not in ("names", "exchange")}
    out = compare(len(calls), p) if p else ok(f"{len(calls)} calls", observed=len(calls))
    if out.status == "pass" and "names" in ctx.params:
        got, want = sorted(c.name or "" for c in calls), sorted(ctx.params["names"])
        if got != want:
            return fail(f"tool names {got} != expected {want}", expected=want, observed=got)
    return out


@check("tool_calls_valid")
def tool_calls_valid(ctx: CheckContext) -> CheckOutcome:
    """Every call has a known name, parseable arguments and arguments valid under JSON Schema 2020-12 AND Draft 7
    (OpenRouter taxonomy: InvalidJson / UnknownName / SchemaMismatch; validator disagreement is reported)."""
    ex = ctx.exchange
    if ex is None:
        return fail("no exchange")
    calls = ctx.response.tool_calls
    if not calls:
        return fail("no tool calls", expected="at least one tool call", observed=ctx.response.finish_reason_norm)
    tools = request_tools(ex)
    results = [validate_call(c, tools) for c in calls]
    bad = [r for r in results if r["error_class"]]
    disagree = [r for r in results if {r["draft2020"], r["draft7"]} == {"valid", "invalid"}]
    note = f"; validators disagree on {[r['name'] for r in disagree]}" if disagree else ""
    if bad:
        first = bad[0]
        return fail(
            f"{first['error_class']} on {first['name']!r}: {first.get('detail') or ''}{note}".strip(),
            expected="valid tool calls",
            observed=results,
        )
    return ok(f"{len(calls)} valid call(s) (2020-12 and draft 7){note}", observed=results)


def _required(schema: dict[str, Any]) -> list[str]:
    req = list(schema.get("required") or [])
    for comb in ("oneOf", "anyOf"):
        if schema.get(comb):
            req += [r for b in schema[comb] if isinstance(b, dict) for r in b.get("required", [])][:1]
    for b in schema.get("allOf") or []:
        req += b.get("required", []) if isinstance(b, dict) else []
    return req


@check("tool_arguments_nonempty_when_required")
def tool_arguments_nonempty_when_required(ctx: CheckContext) -> CheckOutcome:
    """A call whose schema has required properties must not come back with empty ``{}`` arguments (GLM-5#169)."""
    ex = ctx.exchange
    calls = ctx.response.tool_calls
    if ex is None or not calls:
        return skip("no tool calls")
    tools = request_tools(ex)
    bad = [c.name for c in calls if c.arguments_parsed == {} and _required(tools.get(c.name or "", {}))]
    if bad:
        return fail(f"empty arguments {{}} for {bad} although the schema requires properties", observed=bad)
    return ok(f"{len(calls)} call(s) carry their required arguments")


@check("tool_args_echo_prompt")
def tool_args_echo_prompt(ctx: CheckContext) -> CheckOutcome:
    """Arguments equal the JSON the prompt asked for (``exactly these arguments: {...}``), with strict types
    (int stays int, null stays null, strings byte-identical)."""
    ex = ctx.exchange
    if ex is None:
        return fail("no exchange")
    wanted = prompt_arguments(ex)
    if not wanted:
        return skip("the prompt names no exact arguments")
    calls = list(ctx.response.tool_calls)
    for name, want in wanted:
        match = next((c for c in calls if name is None or c.name == name), None)
        if match is None:
            return fail(f"no call to {name or 'any tool'} found", expected=name, observed=[c.name for c in calls])
        if match.parse_error or not isinstance(match.arguments_parsed, dict):
            return fail(f"arguments of {match.name} are not a JSON object", observed=(match.arguments or "")[:200])
        diff = strict_diff(want, match.arguments_parsed)
        if diff:
            return fail(f"{match.name}: {diff}", expected=_short(want), observed=_short(match.arguments_parsed))
        calls.remove(match)
    return ok(f"{len(wanted)} call(s) echo the requested arguments exactly")


@check("tool_arg_startswith")
def tool_arg_startswith(ctx: CheckContext) -> CheckOutcome:
    """A string argument (``arg``, default ``command``) of the first call starts with ``prefix`` (GLM-5#116)."""
    calls = ctx.response.tool_calls
    if not calls:
        return fail("no tool call emitted")
    arg, prefix = ctx.params.get("arg", "command"), ctx.params["prefix"]
    val = (calls[0].arguments_parsed or {}).get(arg) if isinstance(calls[0].arguments_parsed, dict) else None
    if isinstance(val, str) and val.startswith(prefix):
        return ok(f"{arg} starts with {prefix!r}", observed=val[:120])
    return fail(
        f"{arg} does not start with {prefix!r}", expected=prefix, observed=val if val is None else str(val)[:120]
    )


@check("no_marker_leak")
def no_marker_leak(ctx: CheckContext) -> CheckOutcome:
    """No tool-call markup (``<tool_call>``, ``<arg_key>`` ...) in assistant content or reasoning (opencode#6708)."""
    n = ctx.response
    markers = [*MARKERS, *ctx.params.get("extra", [])]
    for where, text in (("content", n.content), ("reasoning", n.reasoning)):
        for m in markers:
            if text and m in text:
                i = text.index(m)
                return fail(f"{m!r} leaked into {where}", expected="no markup", observed=text[max(0, i - 40) : i + 80])
    return ok("no tool-call markup in content or reasoning")


@check("tool_ids_unique")
def tool_ids_unique(ctx: CheckContext) -> CheckOutcome:
    """Tool call ids are non-empty and unique, ``index`` runs 0..n-1, and ``type`` is ``function`` (non-stream body)."""
    ex = ctx.exchange
    calls = ctx.response.tool_calls
    if ex is None or not calls:
        return fail("no tool calls", expected="parallel tool calls")
    ids = [c.id for c in calls]
    if any(not i for i in ids):
        return fail("a tool call has no id", observed=ids)
    if len(set(ids)) != len(ids):
        return fail(f"duplicate tool call ids {ids}", expected="unique ids", observed=ids)
    idx = [c.index for c in calls]
    if idx != list(range(len(calls))):
        return fail(f"index sequence {idx} is not 0..{len(calls) - 1}", observed=idx)
    body = ex.response.body_json if ex.response else None
    if isinstance(body, dict):
        raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
        types = [t.get("type") for t in raw if isinstance(t, dict)]
        if types and any(t != "function" for t in types):
            return fail(f"tool call type is {types}, expected 'function'", observed=types)
    return ok(f"{len(ids)} unique ids, indexes 0..{len(ids) - 1}")


# ------------------------------------------------------------------ aggregate: Wilson interval


@check("pass_rate_wilson", aggregate=True)
def pass_rate_wilson(ctx: CheckContext) -> CheckOutcome:
    """Pass rate over the non-throttled attempts with a Wilson 95% CI; ``gte`` gates the rate and ``lower_gte``
    gates the CI lower bound. Throttled attempts are excluded and reported."""
    scored = [a for a in ctx.attempts if a.status != "throttled"]
    if not scored:
        return skip("every attempt was throttled")
    k, n = sum(1 for a in scored if a.status == "pass"), len(scored)
    lo, hi = wilson_interval(k, n)
    obs = {"passes": k, "n": n, "rate": round(k / n, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    text = f"{k}/{n} passed = {k / n:.1%} (Wilson 95% CI {lo:.1%} to {hi:.1%})"
    gates = {"gte": k / n, "lower_gte": lo}
    for key, val in gates.items():
        if key in ctx.params and val < ctx.params[key]:
            what = "rate" if key == "gte" else "CI lower bound"
            return fail(f"{text}; {what} {val:.1%} is below {ctx.params[key]:.1%}", expected=ctx.params, observed=obs)
    return ok(text, expected=ctx.params, observed=obs)


# ------------------------------------------------------------------ K2VV / Prism replay scores


@check("k2vv_scores")
def k2vv_scores(ctx: CheckContext) -> CheckOutcome:
    """K2VV-comparable scores over a replayed corpus: trigger F1 of ``finish_reason == tool_calls`` against the
    reference, and schema accuracy (valid tool calls / tool_calls responses). Params: ``f1_gte``,
    ``schema_accuracy_gte``."""
    from glmkit.conform.k2vv import score

    obs = score(ctx.exchanges)
    if obs is None:
        return skip("no replayed requests carry a reference")
    f1, acc = obs["f1"], obs["schema_accuracy"]
    text = (
        f"F1 {f1:.3f} (P {obs['precision']:.3f}, R {obs['recall']:.3f}), "
        f"schema accuracy {acc:.3f} over {obs['n']} requests"
    )
    for key, val, label in (("f1_gte", f1, "F1"), ("schema_accuracy_gte", acc, "schema accuracy")):
        if key in ctx.params and val < ctx.params[key]:
            return fail(f"{text}; {label} {val:.3f} is below {ctx.params[key]}", expected=ctx.params, observed=obs)
    return ok(text, expected=ctx.params, observed=obs)
