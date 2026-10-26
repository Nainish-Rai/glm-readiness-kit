"""Checks on the normalized model response (finish reason, reasoning, content, usage, model, stream)."""

from __future__ import annotations

from glmkit.conform.checks import CheckContext, CheckOutcome, check, compare, fail, ok, skip


@check("finish_reason")
def finish_reason(ctx: CheckContext) -> CheckOutcome:
    """Normalized finish reason (stop | tool_calls | length; Anthropic stop_reason mapped) with in / equals."""
    n = ctx.response
    out = compare(n.finish_reason_norm, ctx.params)
    if n.finish_reason and n.finish_reason != n.finish_reason_norm:
        out.message += f" (raw {n.finish_reason!r})"
    return out


@check("reasoning_present")
def reasoning_present(ctx: CheckContext) -> CheckOutcome:
    """Non-empty reasoning (``reasoning_content`` or Anthropic thinking blocks)."""
    r = ctx.response.reasoning
    if r and r.strip():
        return ok(f"{len(r)} chars", observed=len(r))
    return fail("no reasoning in the response", expected="non-empty reasoning", observed=r)


@check("reasoning_absent")
def reasoning_absent(ctx: CheckContext) -> CheckOutcome:
    """No reasoning in the response (thinking skipped)."""
    r = ctx.response.reasoning
    if r and r.strip():
        return fail(f"unexpected reasoning ({len(r)} chars)", expected="no reasoning", observed=r[:200])
    return ok("no reasoning")


@check("content_present")
def content_present(ctx: CheckContext) -> CheckOutcome:
    """Non-empty assistant text (``matches`` param optionally checks it with a regex)."""
    c = ctx.response.content
    if not c or not c.strip():
        return fail("empty content", expected="non-empty content", observed=c)
    if "matches" in ctx.params:
        return compare(c, {"matches": ctx.params["matches"]})
    return ok(f"{len(c)} chars", observed=c[:200])


@check("usage_present")
def usage_present(ctx: CheckContext) -> CheckOutcome:
    """Usage reports prompt and completion tokens > 0."""
    u = ctx.response.usage
    if u is None:
        return fail("no usage object", expected="usage")
    if not (u.prompt_tokens and u.completion_tokens):
        return fail("usage has zero/missing tokens", observed=u.model_dump(exclude={"raw"}))
    return ok(f"prompt={u.prompt_tokens} completion={u.completion_tokens}", observed=u.model_dump(exclude={"raw"}))


@check("model_field_equals_request")
def model_field_equals_request(ctx: CheckContext) -> CheckOutcome:
    """On success the response ``model`` equals the requested model (no silent fallback)."""
    ex = ctx.exchange
    n = ctx.response
    if ex is None or not n.ok:
        return skip("no successful response")
    asked = ex.request.body.get("model") if isinstance(ex.request.body, dict) else None
    if n.model == asked:
        return ok(f"{n.model!r}", expected=asked, observed=n.model)
    return fail(f"asked for {asked!r}, response says {n.model!r}", expected=asked, observed=n.model)


@check("sse_terminates")
def sse_terminates(ctx: CheckContext) -> CheckOutcome:
    """A stream ends properly ([DONE] / message_stop) within the stream timeout, with exactly one finish event."""
    ex = ctx.exchange
    if ex is None or ex.mode != "stream":
        return skip("not a streamed exchange")
    s = ctx.response.stream
    if s is None:
        return skip("response was not an SSE stream")
    if s.timed_out:
        return fail("stream did not finish before the timeout", observed={"events": s.event_count})
    if not s.done_seen:
        return fail("stream ended without [DONE] / message_stop", observed={"events": s.event_count})
    if s.finish_events != 1:
        return fail(f"{s.finish_events} finish events", expected=1, observed=s.finish_events)
    return ok(f"{s.event_count} events, terminated")
