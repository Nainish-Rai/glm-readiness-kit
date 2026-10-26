"""Rewriter chains and ``BridgeCore``, the object a host drives.

Rewriters are plain callables ``(ctx, body) -> body`` that never mutate their input and never do I/O. Each
fix is one small function; adding a fix means appending a function to a chain.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from glmkit.bridge.core.errors import ClassifiedError, ErrorClassifier, TableErrorClassifier, parse_body
from glmkit.bridge.core.events import FixEmitter, FixEvent
from glmkit.bridge.core.middleware import BridgeMiddleware, Reject, call_hook
from glmkit.bridge.core.retry import ParseErrorRetry, RetryPlan, RetryPolicy, ToolFailure
from glmkit.bridge.core.schema import flatten_schema, needs_flatten
from glmkit.bridge.core.stream import StreamRewriter
from glmkit.bridge.core.toolcalls import fix_call, normalize_reasoning_keys
from glmkit.bridge.core.upstream import DISABLED_VALUES, REASONING_KEYS, UpstreamProfile

Body = dict[str, Any]
Rewriter = Callable[["RewriteContext", Body], Body]


@dataclass
class RewriteContext:
    """Per client call. Request rewriters fill ``tool_schemas`` / ``flattened`` and the response side reads them."""

    upstream: UpstreamProfile
    emitter: FixEmitter
    request_id: str = field(default_factory=lambda: "req_" + uuid.uuid4().hex[:12])
    model: str | None = None
    stream: bool = False
    retries: int = 0  # retries already made for this client call
    tool_schemas: dict[str, Any] = field(default_factory=dict)  # name -> ORIGINAL parameters schema
    flattened: dict[str, Any] = field(default_factory=dict)  # name -> original schema of tools we flattened
    extras: dict[str, Any] = field(default_factory=dict)  # middleware scratch space

    def emit(
        self,
        fix_id: str,
        before: str,
        after: str,
        *,
        details: dict[str, Any] | None = None,
        content: dict[str, Any] | None = None,
        ok: bool | None = None,
    ) -> FixEvent:
        return self.emitter.emit(
            fix_id, self.request_id, before, after, upstream=self.upstream.name, details=details, content=content, ok=ok
        )


# ---------------------------------------------------------------------------------------- request rewriters


def _is_disabled(thinking: Any) -> bool:
    if thinking is False:
        return True
    if isinstance(thinking, dict):
        t = thinking.get("type")
        return t is False or str(t).lower() in DISABLED_VALUES
    return isinstance(thinking, str) and thinking.lower() in DISABLED_VALUES


def rewrite_thinking(ctx: RewriteContext, body: Body) -> Body:
    """``thinking: disabled`` on a model that forces thinking -> enabled + ``reasoning_effort: low``."""
    if (
        "thinking" not in body
        or not ctx.upstream.forces_thinking(body.get("model"))
        or not _is_disabled(body["thinking"])
    ):
        return body
    before = json.dumps(body["thinking"])
    thinking = dict(body["thinking"]) if isinstance(body["thinking"], dict) else {}
    thinking["type"] = "enabled"
    body["thinking"] = thinking
    prior = body.get("reasoning_effort")
    if prior is None or str(prior).lower() in DISABLED_VALUES | {"minimal"}:
        body["reasoning_effort"] = "low"
    ctx.emit(
        "thinking-disabled-to-low",
        f"thinking={before}",
        f"thinking={json.dumps(thinking)}, reasoning_effort={body['reasoning_effort']}",
        details={"model": body.get("model")},
    )
    return body


def rewrite_effort(ctx: RewriteContext, body: Body) -> Body:
    """Clamp ``reasoning_effort`` to the values the model accepts (zai glm-5.3: low, high, max)."""
    up = ctx.upstream
    if "reasoning_effort" not in body or not up.clamps_effort(body.get("model")):
        return body
    raw = body["reasoning_effort"]
    val = str(raw).lower()
    if val in up.effort_allowed:
        if raw != val:
            body["reasoning_effort"] = val
        return body
    new = up.effort_map.get(val, up.effort_default)
    body["reasoning_effort"] = new
    ctx.emit(
        "effort-clamp", f"reasoning_effort={raw!r}", f"reasoning_effort={new!r}", details={"model": body.get("model")}
    )
    return body


def rewrite_tool_schemas(ctx: RewriteContext, body: Body) -> Body:
    """Record every tool's original schema; flatten top-level oneOf/anyOf where the upstream needs it."""
    for i, t in enumerate(body.get("tools") or []):
        fn = t.get("function") if isinstance(t, dict) and isinstance(t.get("function"), dict) else None
        if fn is None or not fn.get("name"):
            continue
        ctx.tool_schemas[fn["name"]] = copy.deepcopy(fn.get("parameters") or {})
        if not ctx.upstream.flatten_oneof_tools or not needs_flatten(fn.get("parameters")):
            continue
        keyword = needs_flatten(fn["parameters"])
        flat, hint = flatten_schema(fn["parameters"])
        ctx.flattened[fn["name"]] = copy.deepcopy(fn["parameters"])
        fn["parameters"] = flat
        fn["description"] = (str(fn.get("description") or "") + " " + hint).strip()
        ctx.emit(
            "schema-flatten",
            f"top-level {keyword}",
            f"flat object with {len(flat['properties'])} properties",
            details={"tool": fn["name"], "tool_index": i, "keyword": keyword},
        )
    return body


def rewrite_reasoning_replay(ctx: RewriteContext, body: Body) -> Body:
    """Send assistant-message reasoning the way the upstream expects: verbatim, renamed, or stripped."""
    up = ctx.upstream
    changed = 0
    for m in body.get("messages") or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        present = [k for k in REASONING_KEYS if k in m]
        if not present:
            continue
        text = next((m[k] for k in present if isinstance(m[k], str) and m[k]), None)
        want = None if up.reasoning_replay == "strip" else up.reasoning_field
        if want and present == [want]:
            continue
        for k in present:
            del m[k]
        if want and text is not None:
            m[want] = text
        changed += 1
    if changed:
        mode = up.reasoning_replay if up.reasoning_replay == "strip" else f"as {up.reasoning_field}"
        ctx.emit(
            "reasoning-replay",
            f"{changed} assistant message(s) with reasoning",
            "reasoning removed" if up.reasoning_replay == "strip" else f"reasoning sent {mode}",
            details={"messages": changed, "mode": up.reasoning_replay},
        )
    return body


DEFAULT_REQUEST_REWRITERS: tuple[Rewriter, ...] = (
    rewrite_thinking,
    rewrite_effort,
    rewrite_tool_schemas,
    rewrite_reasoning_replay,
)


# ---------------------------------------------------------------------------------------- response rewriters


def rewrite_response_message(ctx: RewriteContext, body: Body) -> tuple[Body, list[ToolFailure]]:
    """Non-stream response: repair tool-call arguments, normalise the reasoning field."""
    failures: list[ToolFailure] = []
    renamed = False
    for choice in body.get("choices") or []:
        msg = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(msg, dict):
            continue
        renamed |= normalize_reasoning_keys(ctx, msg)
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            out = fix_call(ctx, fn.get("name", ""), fn.get("arguments"), index=i, call_id=tc.get("id"))
            if out.failure:
                failures.append(out.failure)
            elif isinstance(fn, dict):
                fn["arguments"] = out.arguments
                tc["function"] = fn
    if renamed:
        ctx.emit("reasoning-normalize", "reasoning", "reasoning_content")
    return body, failures


# ---------------------------------------------------------------------------------------- the core


class BridgeCore:
    """Everything a host needs, with no I/O. Hosts: ``glmkit.bridge.proxy`` (ASGI), the Anthropic path (07),
    the LiteLLM plugin (08). Policies plug in through ``middleware`` (09)."""

    def __init__(
        self,
        upstream: UpstreamProfile,
        *,
        emitter: FixEmitter | None = None,
        request_rewriters: tuple[Rewriter, ...] | list[Rewriter] = DEFAULT_REQUEST_REWRITERS,
        classifier: ErrorClassifier | None = None,
        retry: RetryPolicy | None = None,
        middleware: list[BridgeMiddleware] | None = None,
    ) -> None:
        self.upstream = upstream
        self.emitter = emitter or FixEmitter()
        self.request_rewriters = list(request_rewriters)
        self.classifier: ErrorClassifier = classifier or TableErrorClassifier()
        self.retry: RetryPolicy = retry or ParseErrorRetry()
        self.middleware = list(middleware or [])

    # -- context
    def new_context(self, body: Body | None = None, *, request_id: str | None = None) -> RewriteContext:
        ctx = RewriteContext(upstream=self.upstream, emitter=self.emitter)
        if request_id:
            ctx.request_id = request_id
        if body:
            ctx.model = body.get("model")
            ctx.stream = bool(body.get("stream"))
        return ctx

    # -- request side
    def rewrite_request(self, ctx: RewriteContext, body: Body) -> Body:
        out = copy.deepcopy(body)
        for rw in self.request_rewriters:
            out = rw(ctx, out)
        return out

    # -- response side
    def rewrite_response(self, ctx: RewriteContext, body: Body) -> tuple[Body, list[ToolFailure]]:
        return rewrite_response_message(ctx, copy.deepcopy(body))

    def new_stream(self, ctx: RewriteContext) -> StreamRewriter:
        return StreamRewriter(ctx)

    # -- errors
    def classify_error(
        self, ctx: RewriteContext, status: int, body_text: str, headers: dict[str, str] | None = None
    ) -> tuple[ClassifiedError, Body]:
        """Map an upstream error. Returns the classification and the JSON body to send to the client."""
        body = parse_body(body_text)
        cls = self.classifier.classify(status, body, headers)
        err: dict[str, Any] = dict((body or {}).get("error") or {}) if isinstance(body, dict) else {}
        if not isinstance(body, dict) or "error" not in body:
            err = {"code": cls.code, "message": cls.message or "Upstream error"}
        err.setdefault("message", cls.message)
        err.setdefault("type", cls.error_type)
        out = {"error": err}
        if cls.sanitized:
            ctx.emit(
                "error-sanitize",
                f"HTTP {status}, no JSON body",
                f"HTTP {cls.status}, clean error body",
                details={"upstream_status": status},
            )
        elif cls.changed or cls.retry_after_s is not None:
            ctx.emit(
                "error-classify",
                f"HTTP {status} code={cls.code}",
                f"HTTP {cls.status} {cls.kind}, retryable={cls.retryable}, retry-after={cls.retry_after_s}",
                details={
                    "code": cls.code,
                    "kind": cls.kind,
                    "retryable": cls.retryable,
                    "retry_after_s": cls.retry_after_s,
                },
            )
        return cls, out

    # -- retry
    def plan_retry(
        self, ctx: RewriteContext, request: Body, assistant_message: Body, failures: list[ToolFailure]
    ) -> RetryPlan | None:
        """A plan to re-send, or None. Emits ``tool-args-retry`` when a plan is returned."""
        plan = self.retry.plan_after_tool_failure(request, assistant_message, failures, ctx.retries)
        if plan is not None:
            ctx.retries += 1
            ctx.emit(
                "tool-args-retry",
                f"{len(failures)} tool call(s) with invalid arguments",
                "upstream re-asked once with the parse error as the tool result",
                details={"tools": [f.name for f in failures], "parse_errors": [f.parse_error for f in failures]},
            )
        return plan

    def unrepaired(self, ctx: RewriteContext, failures: list[ToolFailure]) -> Body:
        """Emit ``tool-args-unrepaired`` and return the client-facing error body (HTTP 502)."""
        ctx.emit(
            "tool-args-unrepaired",
            f"{len(failures)} tool call(s) with invalid arguments",
            "error returned to the client; no arguments substituted",
            details={"tools": [f.name for f in failures], "parse_errors": [f.parse_error for f in failures]},
        )
        names = ", ".join(f.name for f in failures)
        return {
            "error": {
                "message": f"Upstream returned unusable arguments for tool call(s): {names}. "
                f"The bridge retried once and could not repair them.",
                "type": "api_error",
                "code": "bridge_tool_arguments_invalid",
            }
        }

    # -- middleware (async because a policy may need I/O)
    async def before_request(self, ctx: RewriteContext, body: Body) -> Body | Reject:
        for mw in self.middleware:
            res = await call_hook(mw, "before_request", ctx, body)
            if isinstance(res, Reject):
                return res
            if isinstance(res, dict):
                body = res
        return body

    async def after_response(self, ctx: RewriteContext, body: Body) -> Body | Reject:
        for mw in self.middleware:
            res = await call_hook(mw, "after_response", ctx, body)
            if isinstance(res, Reject):
                return res
            if isinstance(res, dict):
                body = res
        return body

    async def on_stream_event(self, ctx: RewriteContext, events: list[Any]) -> list[Any] | Reject:
        for mw in self.middleware:
            nxt: list[Any] = []
            for ev in events:
                res = await call_hook(mw, "on_stream_event", ctx, ev)
                if isinstance(res, Reject):
                    return res
                nxt.extend(res if isinstance(res, list) else [ev])
            events = nxt
        return events

    async def on_error(self, ctx: RewriteContext, cls: ClassifiedError) -> ClassifiedError:
        for mw in reversed(self.middleware):
            res = await call_hook(mw, "on_error", ctx, cls)
            if isinstance(res, ClassifiedError):
                cls = res
        return cls

    async def on_finish(self, ctx: RewriteContext, usage: dict[str, Any] | None) -> None:
        for mw in self.middleware:
            await call_hook(mw, "on_finish", ctx, usage)
