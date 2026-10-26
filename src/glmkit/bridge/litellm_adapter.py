"""LiteLLM adapter logic, with no LiteLLM import: plain dicts in, plain dicts out.

``glmkit.bridge.litellm_plugin`` is the thin ``CustomLogger`` shell around ``LiteLLMBridge``; keeping the logic
here means it is unit-tested without LiteLLM installed (LiteLLM and the dev group cannot share one environment,
see pyproject.toml). Fixes are the core's own rewriters (``glmkit.bridge.core``); this module only maps LiteLLM's
call kwargs / response objects onto the core's JSON bodies.

What it handles (research/02-bridge.md section 3): request rewriters in ``async_pre_call_deployment_hook``
(forced thinking, effort clamp, schema flatten, reasoning replay), non-stream tool-argument repair in
``async_post_call_success_deployment_hook``, stateful stream repair in ``async_post_call_streaming_iterator_hook``
(proxy only), upstream error mapping in ``async_post_call_failure_hook``, and the retry-with-parse-error through
LiteLLM's chat-completion agentic-loop hooks (non-stream).

Privacy: no message content is logged or recorded unless ``log_content=True``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from glmkit.bridge.core import BridgeCore, FixEmitter, RewriteContext, UpstreamProfile, get_upstream
from glmkit.bridge.core.retry import ToolFailure
from glmkit.bridge.recorder import RequestRecord, SessionRecorder
from glmkit.contracts.profile import EndpointProfile
from glmkit.redact import Redactor

SOURCE = "litellm"
MAX_LIVE_CALLS = 2048
ENV_FLAVOR = "GLMKIT_BRIDGE_FLAVOR"  # zai | zai-plan | cerebras | generic (default: by provider prefix)
ENV_OUT = "GLMKIT_BRIDGE_OUT"  # results folder for the Bridge tab (default ./results)
ENV_LOG_CONTENT = "GLMKIT_BRIDGE_LOG_CONTENT"  # "1" to also record message content (off by default)
ENV_MODELS = "GLMKIT_BRIDGE_MODELS"  # extra regex of model names to treat as GLM (default glm-*)
RETRY_FLAG = "glmkit_retry"  # metadata flag on the one retry call, so the hooks leave it alone

# kwargs we lift into the core's body (everything else is left untouched)
BODY_KEYS = ("messages", "tools", "tool_choice", "thinking", "reasoning_effort")
# params LiteLLM's zai provider mishandles when passed top-level (see docs/bridge-litellm.md): sent via extra_body
EXTRA_BODY_KEYS = ("thinking", "reasoning_effort")


def provider_of(model: str | None) -> str:
    return (model or "").split("/", 1)[0].lower() if "/" in (model or "") else ""


@dataclass
class Tracked:
    ctx: RewriteContext
    sent: dict[str, Any]
    request: RequestRecord
    t0: float
    conn: dict[str, Any] = field(
        default_factory=dict
    )  # where to re-send for a stream retry; in memory only, never logged


@dataclass
class LiveCalls:
    """Per-call state keyed by ``litellm_call_id``, bounded."""

    items: OrderedDict[str, Tracked] = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, key: str, value: Tracked) -> None:
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > MAX_LIVE_CALLS:
                self.items.popitem(last=False)

    def get(self, key: str | None) -> Tracked | None:
        with self.lock:
            return self.items.get(key) if key else None

    def pop(self, key: str | None) -> Tracked | None:
        with self.lock:
            return self.items.pop(key, None) if key else None


class LiteLLMBridge:
    def __init__(
        self,
        *,
        flavor: str | None = None,
        out_dir: Path | None = None,
        log_content: bool = False,
        model_pattern: str | None = None,
    ) -> None:
        self.flavor = flavor
        self.log_content = log_content
        self.model_re = re.compile(model_pattern or r"glm-.*|.*[/-]glm-.*", re.IGNORECASE)
        self.out_dir = out_dir or Path("results")
        profile = EndpointProfile(
            name="litellm",
            title="LiteLLM gateway",
            provider="generic",
            base_url="litellm://gateway",
            protocol="openai_chat",
            endpoint_class="api",
            model="glm",
            api_key_required=False,
        )
        self.recorder = SessionRecorder(
            profile,
            self.out_dir,
            flavor=flavor or "auto",
            config={"log_content": log_content},
            redactor=Redactor([]),
            source=SOURCE,
        )
        self.emitter = FixEmitter(log_content=log_content, sinks=[self.recorder.on_event])
        self.live = LiveCalls()
        self._cores: dict[str, BridgeCore] = {}

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LiteLLMBridge:
        e = os.environ if env is None else env
        return cls(
            flavor=e.get(ENV_FLAVOR) or None,
            out_dir=Path(e.get(ENV_OUT) or "results"),
            log_content=e.get(ENV_LOG_CONTENT, "").lower() in ("1", "true", "yes"),
            model_pattern=e.get(ENV_MODELS) or None,
        )

    # -- routing
    def upstream_for(self, model: str | None) -> UpstreamProfile | None:
        """The behaviour profile for a LiteLLM model string (``zai/glm-5.3``), or None to leave the call alone."""
        base = (model or "").rsplit("/", 1)[-1]
        if not self.model_re.fullmatch(base) and not self.model_re.fullmatch(model or ""):
            return None
        if self.flavor:
            return get_upstream(self.flavor)
        prov = provider_of(model)
        if prov == "cerebras":
            return get_upstream("cerebras")
        if prov in ("zai", "z_ai", "zhipuai", "openai", "hosted_vllm", ""):
            return get_upstream("zai")
        return get_upstream("generic")

    def core_for(self, up: UpstreamProfile) -> BridgeCore:
        if up.name not in self._cores:
            self._cores[up.name] = BridgeCore(up, emitter=self.emitter)
        return self._cores[up.name]

    # -- request
    @staticmethod
    def call_id(kwargs: dict[str, Any] | None) -> str | None:
        k = kwargs or {}
        return k.get("litellm_call_id") or (k.get("litellm_params") or {}).get("litellm_call_id")

    @staticmethod
    def body_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": kwargs.get("model")}
        extra = kwargs.get("extra_body") if isinstance(kwargs.get("extra_body"), dict) else {}
        for k in BODY_KEYS:
            if kwargs.get(k) is not None:
                body[k] = kwargs[k]
            elif k in extra:
                body[k] = extra[k]
        return body

    def rewrite_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        """``async_pre_call_deployment_hook``: returns modified kwargs, or None when the call is not ours."""
        model = kwargs.get("model")
        up = self.upstream_for(model)
        if up is None or not isinstance(kwargs.get("messages"), list) or (kwargs.get("metadata") or {}).get(RETRY_FLAG):
            return None
        core = self.core_for(up)
        body = self.body_from_kwargs(kwargs)
        ctx = core.new_context({**body, "stream": bool(kwargs.get("stream"))}, request_id=self._request_id(kwargs))
        call_id = self.call_id(kwargs)
        prior = self.live.get(call_id)
        if prior is not None:  # router retry of the same call: keep the ORIGINAL schemas, do not double-count
            ctx.retries = prior.ctx.retries
        sent = core.rewrite_request(ctx, body)
        if prior is not None:
            ctx.tool_schemas.update(prior.ctx.tool_schemas)
            ctx.flattened.update(prior.ctx.flattened)
        out = dict(kwargs)
        extra = dict(kwargs.get("extra_body") or {})
        for k in BODY_KEYS:
            if k in EXTRA_BODY_KEYS:
                out.pop(k, None)
                if k in sent:
                    extra[k] = sent[k]
                else:
                    extra.pop(k, None)
            elif k in sent:
                out[k] = sent[k]
        if extra:
            out["extra_body"] = extra
        rec = RequestRecord(
            request_id=ctx.request_id,
            ts=_now(),
            path="litellm",
            stream=ctx.stream,
            model=model,
        )
        conn = {k: kwargs[k] for k in ("model", "api_base", "api_key", "custom_llm_provider") if kwargs.get(k)}
        self.live.put(call_id or ctx.request_id, Tracked(ctx, sent, rec, time.monotonic(), conn))
        return out

    @staticmethod
    def _request_id(kwargs: dict[str, Any]) -> str | None:
        cid = LiteLLMBridge.call_id(kwargs)
        return f"req_{cid[:12]}" if cid else None

    # -- non-stream response
    def fix_response(self, request_data: dict[str, Any], response: Any) -> Any | None:
        """``async_post_call_success_deployment_hook``. Repairs tool arguments in place. Returns the response, or
        None when nothing applies. Calls whose arguments cannot be repaired are left for ``retry_plan``."""
        tracked = self.live.get(self.call_id(request_data))
        if tracked is None or not hasattr(response, "choices"):
            return None
        core = self.core_for_ctx(tracked.ctx)
        data = _dump(response)
        fixed, failures = core.rewrite_response(tracked.ctx, data)
        # by now the agentic-loop hook has already had its one retry; what is left is final
        _apply_tool_arguments(response, fixed)
        status = 200
        if failures:
            self._resolve_failures(tracked, core, failures)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if tracked.ctx.extras.get("unrepaired"):
            status = 502
        self.finish(tracked, status, usage)
        return response

    def _resolve_failures(self, tracked: Tracked, core: BridgeCore, failures: list[ToolFailure]) -> None:
        for f in failures:
            if f.kind == "schema":
                tracked.ctx.emit(
                    "tool-args-schema-mismatch",
                    "arguments parse",
                    "passed through unchanged",
                    details={"tool": f.name, "index": f.index, "issues": [f.parse_error]},
                )
        if any(f.kind == "parse" for f in failures):
            tracked.ctx.extras["unrepaired"] = core.unrepaired(tracked.ctx, failures)  # client-facing error body

    def core_for_ctx(self, ctx: RewriteContext) -> BridgeCore:
        return self.core_for(ctx.upstream)

    # -- retry with the parse error (agentic-loop hooks)
    def failures_in(self, response: Any, kwargs: dict[str, Any], messages: list[dict[str, Any]]) -> Any | None:
        """``async_should_run_chat_completion_agentic_loop``: a plan when the raw response has unparseable
        tool-call arguments and no retry was made yet. Runs BEFORE ``fix_response`` for the same response."""
        model = kwargs.get("model") or ""
        up = self.upstream_for(model)
        if up is None or not hasattr(response, "choices"):
            return None
        meta = kwargs.get("metadata") or {}
        if meta.get(RETRY_FLAG):
            return None
        core = self.core_for(up)
        ctx = core.new_context({"model": model}, request_id=self._request_id(kwargs))
        tracked = self.live.get(self.call_id(kwargs))
        if tracked is not None:
            ctx = tracked.ctx
        _, failures = core.rewrite_response(_scratch_ctx(core, ctx), _dump(response))
        parse = [f for f in failures if f.kind == "parse"]
        if not parse:
            return None
        msg = _first_message(_dump(response))
        sent = dict(tracked.sent) if tracked else {"messages": messages}
        sent["messages"] = copy.deepcopy(messages)
        plan = core.plan_retry(ctx, sent, msg, parse)
        if plan is None:
            return None
        return plan

    async def retry_stream(self, tracked: Tracked, rw: Any, complete: Any) -> list[Any] | None:
        """The stream rewriter held a call with unusable arguments: re-ask once (non-stream, parse error shown to the
        model) through ``complete(request_body, conn) -> response dict`` and return the events to release, or None
        when it could not be fixed (the caller returns an error)."""
        core = self.core_for_ctx(tracked.ctx)
        plan = core.plan_retry(tracked.ctx, tracked.sent, rw.assistant_message, rw.failures)
        body2: dict[str, Any] | None = None
        failures2 = rw.failures
        if plan is not None:
            try:
                data = await complete(plan.request, tracked.conn)
                body2, failures2 = core.rewrite_response(tracked.ctx, data)
            except Exception:  # a failed retry becomes the unrepaired error below
                body2 = None
        if body2 is not None and not any(f.kind == "parse" for f in failures2):
            self._resolve_failures(tracked, core, failures2)
            return rw.resolve(((body2.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or [])
        self._resolve_failures(tracked, core, failures2)
        return None

    # -- errors
    def map_failure(
        self, request_data: dict[str, Any], exc: Exception
    ) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        """``async_post_call_failure_hook``: classify a Z.ai error body carried in LiteLLM's exception text. Returns
        ``(status, error, headers)`` when the status should change, else None. Only JSON bodies are classified."""
        tracked = self.live.pop(self.call_id(request_data))
        if tracked is None:
            return None
        status = getattr(exc, "status_code", None)
        body_text = _error_body_text(exc)
        if not isinstance(status, int) or body_text is None:
            self.finish(tracked, status if isinstance(status, int) else 500)
            return None
        core = self.core_for_ctx(tracked.ctx)
        cls, body = core.classify_error(tracked.ctx, status, body_text, {})
        self.finish(tracked, cls.status)
        if not (cls.changed or cls.retry_after_s is not None):
            return None
        headers = {"x-should-retry": "true" if cls.retryable else "false"}
        if cls.retry_after_s is not None:
            headers["retry-after"] = str(cls.retry_after_s)
        return cls.status, body["error"], headers

    def finish(self, tracked: Tracked, status: int, usage: dict[str, Any] | None = None) -> None:
        rec = tracked.request
        rec.status = status
        rec.duration_ms = round((time.monotonic() - tracked.t0) * 1000, 1)
        rec.retried = tracked.ctx.retries > 0
        rec.fixes = [e.fix_id for e in self.emitter.for_request(tracked.ctx.request_id)]
        rec.usage = usage
        self.recorder.add_request(rec)

    def flush(self, *, final: bool = False) -> None:
        self.recorder.flush(final=final)


def _error_body_text(exc: Exception) -> str | None:
    """The upstream's JSON error body as LiteLLM kept it (``exc.body`` is the inner ``error`` object), else None."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body:
        return json.dumps(body if "error" in body else {"error": body})
    text = str(getattr(exc, "message", None) or exc)
    start = text.find('{"')
    return text[start:] if start >= 0 else None


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _dump(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return copy.deepcopy(obj)
    for name in ("model_dump", "dict"):
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn()
    raise TypeError(f"cannot convert {type(obj).__name__} to a dict")


def _first_message(data: dict[str, Any]) -> dict[str, Any]:
    return ((data.get("choices") or [{}])[0].get("message")) or {}


def _scratch_ctx(core: BridgeCore, ctx: RewriteContext) -> RewriteContext:
    """A context that shares the original schemas but whose events are not emitted (probing only)."""
    probe = RewriteContext(upstream=ctx.upstream, emitter=FixEmitter(), request_id=ctx.request_id)
    probe.tool_schemas = ctx.tool_schemas
    probe.flattened = ctx.flattened
    return probe


def _apply_tool_arguments(response: Any, fixed: dict[str, Any]) -> None:
    """Write repaired ``function.arguments`` back onto LiteLLM's response object (only the arguments change)."""
    for ch, fch in zip(getattr(response, "choices", []) or [], fixed.get("choices") or [], strict=False):
        msg = getattr(ch, "message", None)
        calls = getattr(msg, "tool_calls", None) or []
        fcalls = (fch.get("message") or {}).get("tool_calls") or []
        for tc, ftc in zip(calls, fcalls, strict=False):
            new = (ftc.get("function") or {}).get("arguments")
            if new is not None and getattr(tc.function, "arguments", None) != new:
                tc.function.arguments = new
