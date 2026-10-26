"""HTTP status and error-contract checks (research/01-conformance.md §2.5, §6)."""

from __future__ import annotations

import json
import re

from glmkit.conform.checks import CheckContext, CheckOutcome, check, compare, fail, ok, skip

INTERNAL_LEAK = [
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r'File "[^"]+\.py", line \d+'),
    re.compile(r"'NoneType' object|NoneType"),
    re.compile(r"object has no attribute"),
    re.compile(r"\b(?:KeyError|TypeError|AttributeError|ValueError|IndexError): "),
    re.compile(r"not supported between instances of"),
    re.compile(r"jinja2\.\w+Error|UndefinedError"),
    re.compile(r"\bat [\w$.]+\([\w$]+\.java:\d+\)"),
]


def _no_response(ctx: CheckContext) -> CheckOutcome | None:
    ex = ctx.exchange
    if ex is None:
        return fail("no exchange recorded")
    if ex.response is None:
        return fail(
            f"no HTTP response ({ex.transport_error or 'unknown transport error'})", observed=ex.transport_error
        )
    return None


@check("http_status")
def http_status(ctx: CheckContext) -> CheckOutcome:
    """HTTP status compared with equals / in / not_in / lt / gte ..."""
    return _no_response(ctx) or compare(ctx.response.http_status, ctx.params)


@check("no_5xx")
def no_5xx(ctx: CheckContext) -> CheckOutcome:
    """The endpoint must never answer with a 5xx (a server crash on a documented input)."""
    bad = _no_response(ctx)
    if bad:
        return bad
    status = ctx.response.http_status
    if status is not None and status >= 500:
        return fail(f"HTTP {status}", expected="< 500", observed=status)
    return ok(f"HTTP {status}", expected="< 500", observed=status)


@check("error_code")
def error_code(ctx: CheckContext) -> CheckOutcome:
    """Z.ai business code (error.code, string or integer) compared as a string: ``in: ["1210"]``."""
    bad = _no_response(ctx)
    if bad:
        return bad
    n = ctx.response
    out = compare(n.error_code, ctx.params, normalize=lambda v: None if v is None else str(v))
    if out.status == "pass" and n.error_code_type:
        out.message = f"{n.error_code!r} ({n.error_code_type} in body)"
    return out


@check("error_shape")
def error_shape(ctx: CheckContext) -> CheckOutcome:
    """A non-2xx body is JSON with an ``error`` object carrying a code and a non-empty message."""
    bad = _no_response(ctx)
    if bad:
        return bad
    ex = ctx.exchange
    assert ex is not None and ex.response is not None
    if ex.response.status is not None and ex.response.status < 400:
        return skip("response is not an error")
    body = ex.response.body_json
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        observed = (ex.response.body_text or "")[:200]
        return fail(
            "error body is not JSON with an 'error' object",
            expected='{"error": {"code", "message"}}',
            observed=observed,
        )
    err = body["error"]
    missing = [k for k in ("code", "message") if err.get(k) in (None, "")]
    if ctx.params.get("require_code", True) is False and "code" in missing:
        missing.remove("code")
    if missing:
        return fail(f"error object missing {missing}", expected=["code", "message"], observed=err)
    return ok(f"code={err.get('code')!r} message present", observed={"code": err.get("code")})


@check("no_internal_leak")
def no_internal_leak(ctx: CheckContext) -> CheckOutcome:
    """No stack traces or Python/Java exception text in the response body or stream."""
    ex = ctx.exchange
    if ex is None or ex.response is None:
        return skip("no response to inspect")
    texts = [ex.response.body_text or ""]
    texts += [
        json.dumps(e.data, ensure_ascii=False) if not isinstance(e.data, str) else e.data
        for e in ex.response.events or []
    ]
    blob = "\n".join(texts)
    patterns = INTERNAL_LEAK + [re.compile(p) for p in ctx.params.get("extra_patterns", [])]
    for rx in patterns:
        m = rx.search(blob)
        if m:
            start = max(0, m.start() - 60)
            return fail(f"internal error text leaked: {m.group(0)!r}", observed=blob[start : m.end() + 120])
    return ok("no exception text")
