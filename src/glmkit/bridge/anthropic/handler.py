"""Starlette routes of the Anthropic Messages path: ``POST .../v1/messages`` and ``.../v1/messages/count_tokens``.

The upstream is chosen by the endpoint profile's protocol:

* ``anthropic_messages`` (Z.ai's Anthropic-compatible endpoint): the request is fixed at the Anthropic level and
  forwarded; the response and the SSE stream are fixed on the way back.
* ``openai_chat`` (any OpenAI-compatible GLM host): the request is fixed at the Anthropic level, translated to chat
  completions, run through the core's rewriters and stream rewriter, and translated back.

Middleware (``ProxyConfig.middleware``) sees the *upstream* wire shape: Anthropic bodies and events for the native
upstream, OpenAI bodies and chunks for the translated one. ``ctx.extras["protocol"] == "anthropic"`` and
``ctx.extras["wire"]`` tell it which; ``on_finish`` always receives OpenAI-shaped usage so spend accounting works
unchanged. Fix events from this path carry ``details["protocol"] == "anthropic"`` (the Bridge tab's filter).

Logging: request id, path, status and fix ids only; auth headers and message content are never logged unless
``log_content`` is set, and keys are redacted.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from glmkit.bridge.anthropic import rewriters as rw
from glmkit.bridge.anthropic import translate as tr
from glmkit.bridge.anthropic import websearch as ws
from glmkit.bridge.anthropic.tokens import Features, TokenCalibrator, features, replay_preserved
from glmkit.bridge.core import Reject, RewriteContext
from glmkit.bridge.core.errors import ClassifiedError
from glmkit.bridge.core.pipeline import rewrite_reasoning_replay
from glmkit.bridge.core.retry import ToolFailure
from glmkit.bridge.core.toolcalls import fix_call
from glmkit.bridge.proxy import BRIDGE_FIXES_HEADER, BRIDGE_ID_HEADER, ProxyConfig, create_app
from glmkit.bridge.recorder import RequestRecord
from glmkit.client.sse import SSEEvent, SSEParser

log = logging.getLogger("glmkit.bridge")
FORWARD_HEADERS = ("accept", "user-agent")
ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class AnthropicOptions:
    search_backend: ws.SearchBackend | None = None  # None: web_search is stripped cleanly
    default_max_tokens: int = rw.DEFAULT_MAX_TOKENS
    calibrator: TokenCalibrator = field(default_factory=TokenCalibrator)
    extra_rewriters: tuple[rw.Rewriter, ...] = ()  # appended to the Anthropic-level chain (issue 08/09 hook)


class TaggedEmitter:
    """Wraps a ``FixEmitter`` so every event of an Anthropic-path call carries ``details.protocol``."""

    def __init__(self, inner: Any, protocol: str = "anthropic") -> None:
        self._inner = inner
        self._protocol = protocol

    def emit(
        self, fix_id: str, request_id: str, before: str, after: str, *, details: dict[str, Any] | None = None, **kw: Any
    ) -> Any:
        return self._inner.emit(
            fix_id, request_id, before, after, details={"protocol": self._protocol, **(details or {})}, **kw
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Fail(Exception):
    def __init__(self, response: Response, upstream_status: int | None = None) -> None:
        self.response = response
        self.upstream_status = upstream_status


class _StreamRejected(Exception):
    def __init__(self, reject: Reject) -> None:
        self.reject = reject


@dataclass
class _Call:
    ctx: RewriteContext
    rec: RequestRecord
    t0: float
    headers: dict[str, str]
    core: Any
    emitter: Any
    recorder: Any
    up_status: int | None = None
    usage: dict[str, Any] | None = None  # Anthropic-shaped, as the client sees it


class AnthropicBridge:
    def __init__(self, cfg: ProxyConfig, options: AnthropicOptions, client: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.opt = options
        self.client = client
        self.native = cfg.profile.protocol == "anthropic_messages"
        self.url = cfg.profile.url
        self.key = cfg.profile.api_key()

    # ------------------------------------------------------------------------------------ routes
    def routes(self) -> list[BaseRoute]:
        out: list[BaseRoute] = [
            Route("/v1/messages/count_tokens", self.count_tokens, methods=["POST"]),
            Route("/{prefix:path}/v1/messages/count_tokens", self.count_tokens, methods=["POST"]),
            Route("/v1/messages", self.messages, methods=["POST"]),
            Route("/{prefix:path}/v1/messages", self.messages, methods=["POST"]),
        ]
        if self.native:  # the OpenAI route would post a chat body to an Anthropic URL
            out += [
                Route("/chat/completions", self.wrong_protocol, methods=["POST"]),
                Route("/{prefix:path}/chat/completions", self.wrong_protocol, methods=["POST"]),
            ]
        return out

    async def wrong_protocol(self, _: Request) -> Response:
        return JSONResponse(
            {
                "error": {
                    "message": "This bridge forwards to an Anthropic Messages upstream; use /v1/messages.",
                    "type": "invalid_request_error",
                    "code": "bridge_wrong_protocol",
                }
            },
            status_code=400,
        )

    # ------------------------------------------------------------------------------------ plumbing
    def _call(self, request: Request, body: dict[str, Any]) -> _Call:
        st = request.app.state
        core = st.core
        ctx = core.new_context(body)
        ctx.emitter = TaggedEmitter(core.emitter)
        ctx.extras.update(
            protocol="anthropic",
            wire="anthropic_messages" if self.native else "openai_chat",
            default_max_tokens=self.opt.default_max_tokens,
            search_backend=self.opt.search_backend,
        )
        rec = RequestRecord(
            request_id=ctx.request_id,
            ts=datetime.now(UTC).isoformat(),
            path=request.url.path,
            stream=ctx.stream,
            model=ctx.model,
        )
        return _Call(ctx, rec, time.monotonic(), self._headers(request), core, st.emitter, st.recorder)

    def _headers(self, request: Request) -> dict[str, str]:
        h = {"content-type": "application/json"}
        for name in FORWARD_HEADERS:
            if name in request.headers:
                h[name] = request.headers[name]
        for name, value in request.headers.items():  # X-GLMKit-* steer the mock server
            if name.lower().startswith("x-glmkit-") and not name.lower().startswith("x-glmkit-bridge"):
                h[name] = value
        if self.native:
            h["anthropic-version"] = request.headers.get("anthropic-version", ANTHROPIC_VERSION)
        h.update(self.cfg.profile.headers)
        if self.key:
            if self.cfg.profile.auth_style == "bearer":
                h["authorization"] = f"Bearer {self.key}"
            else:
                h["x-api-key"] = self.key
        else:  # no upstream key configured: pass the caller's credentials through, untouched and unlogged
            auth, xkey = request.headers.get("authorization"), request.headers.get("x-api-key")
            if self.native or self.cfg.profile.auth_style != "bearer":
                if xkey:
                    h["x-api-key"] = xkey
                if auth:
                    h["authorization"] = auth
            elif auth or xkey:
                h["authorization"] = auth or f"Bearer {xkey}"
        return h

    def _finish(self, c: _Call, status: int) -> None:
        rec = c.rec
        rec.status, rec.upstream_status = status, c.up_status
        rec.duration_ms = round((time.monotonic() - c.t0) * 1000, 1)
        rec.retried = c.ctx.retries > 0
        rec.fixes = [e.fix_id for e in c.emitter.for_request(c.ctx.request_id)]
        rec.usage = tr.openai_usage(c.usage)
        c.recorder.add_request(rec)
        log.info("%s %s -> %s fixes=%s", c.ctx.request_id, rec.path, status, ",".join(rec.fixes) or "-")

    def _fixes_header(self, c: _Call) -> str:
        return ",".join(dict.fromkeys(e.fix_id for e in c.emitter.for_request(c.ctx.request_id)))

    def _plain_error(self, c: _Call | None, status: int, message: str, etype: str | None = None) -> Response:
        rid = c.ctx.request_id if c else None
        headers = {"request-id": rid, BRIDGE_ID_HEADER: rid} if rid else {}
        return JSONResponse(tr.error_body(status, message, rid, etype), status_code=status, headers=headers)

    def _error_response(self, c: _Call, cls: ClassifiedError, body: dict[str, Any]) -> Response:
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        message = str(err.get("message") or cls.message or "Upstream error")
        etype = cls.error_type if cls.error_type and cls.error_type != "api_error" else tr.error_type(cls.status)
        headers = {
            "x-should-retry": "true" if cls.retryable else "false",
            BRIDGE_ID_HEADER: c.ctx.request_id,
            "request-id": c.ctx.request_id,
        }
        if cls.retry_after_s is not None:
            headers["retry-after"] = str(cls.retry_after_s)
        return JSONResponse(
            tr.error_body(cls.status, message, c.ctx.request_id, etype), status_code=cls.status, headers=headers
        )

    def _reject(self, c: _Call, rej: Reject) -> Response:
        if rej.fix_id:
            c.ctx.emit(
                rej.fix_id, "request", f"rejected with HTTP {rej.status}", details={"message": rej.message}, ok=False
            )
        headers = {BRIDGE_ID_HEADER: c.ctx.request_id, "request-id": c.ctx.request_id}
        if rej.retry_after_s is not None:
            headers["retry-after"] = str(rej.retry_after_s)
        return JSONResponse(
            tr.error_body(rej.status, rej.message, c.ctx.request_id, rej.error_type),
            status_code=rej.status,
            headers=headers,
        )

    async def _upstream_error(self, c: _Call, resp: httpx.Response) -> Response:
        cls, body = c.core.classify_error(c.ctx, resp.status_code, resp.text, dict(resp.headers))
        cls = await c.core.on_error(c.ctx, cls)
        return self._error_response(c, cls, body)

    async def _post(self, c: _Call, wire: dict[str, Any], *, stream: bool = False) -> httpx.Response:
        data = json.dumps(wire, ensure_ascii=False).encode()
        try:
            if stream:
                up = await self.client.send(
                    self.client.build_request("POST", self.url, content=data, headers=c.headers), stream=True
                )
            else:
                up = await self.client.post(self.url, content=data, headers=c.headers)
        except httpx.TimeoutException:
            raise _Fail(self._plain_error(c, 504, "Upstream timed out.", "timeout_error")) from None
        except httpx.HTTPError as e:
            raise _Fail(
                self._plain_error(c, 502, f"Upstream connection failed ({type(e).__name__}).", "api_error")
            ) from None
        c.up_status = up.status_code
        if up.status_code >= 400:
            if stream:
                await up.aread()
                await up.aclose()
            raise _Fail(await self._upstream_error(c, up), up.status_code)
        return up

    def _features(self, body: dict[str, Any]) -> Features:
        wire = "anthropic_messages" if self.native else "openai_chat"
        return features(body, replay_preserved(body, wire=wire, default=self.cfg.upstream.preserved_thinking))

    # ------------------------------------------------------------------------------------ request side
    def _prepare(self, c: _Call, body: dict[str, Any]) -> dict[str, Any]:
        sent = copy.deepcopy(body)
        chain = [*rw.COMMON, *(rw.NATIVE if self.native else ()), *self.opt.extra_rewriters]
        for fn in chain:
            sent = fn(c.ctx, sent)
        c.ctx.extras["est_input"] = self.opt.calibrator.estimate_features(sent.get("model"), self._features(sent))
        return sent

    async def _wire_request(self, c: _Call, sent: dict[str, Any]) -> dict[str, Any]:
        wire = sent if self.native else c.core.rewrite_request(c.ctx, tr.to_openai_request(sent))
        mw = await c.core.before_request(c.ctx, wire)
        if isinstance(mw, Reject):
            raise _Fail(self._reject(c, mw))
        return mw

    # ------------------------------------------------------------------------------------ non-stream
    def _usage_fix(self, c: _Call, msg: dict[str, Any], feats: Features) -> None:
        usage = msg.setdefault("usage", {})
        if tr.total_input(usage) == 0:
            n = self.opt.calibrator.estimate_features(c.ctx.model, feats)
            usage["input_tokens"] = n
            c.ctx.emit(
                "anthropic-usage-inject",
                "usage.input_tokens is 0",
                f"usage.input_tokens={n} (calibrated estimate)",
                details={"estimate": n},
            )
        else:
            self.opt.calibrator.observe(c.ctx.model, feats, tr.total_input(usage))

    def _fix_native_message(self, c: _Call, msg: dict[str, Any]) -> None:
        for b in msg.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and not isinstance(b.get("input"), dict):
                raw = b.get("input")
                out = fix_call(c.ctx, b.get("name", ""), raw, call_id=b.get("id"))
                if out.failure:
                    raise _Fail(
                        self._plain_error(
                            c, 502, f"Upstream returned unusable input for tool {b.get('name')}.", "api_error"
                        ),
                        c.up_status,
                    )
                b["input"] = json.loads(out.arguments)

    async def _complete(self, c: _Call, sent: dict[str, Any]) -> dict[str, Any]:
        """One non-stream upstream call -> an Anthropic message (all fixes applied). Raises ``_Fail``."""
        feats = self._features(sent)
        wire = await self._wire_request(c, sent)
        up = await self._post(c, wire)
        try:
            data = up.json()
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError:
            cls, err = c.core.classify_error(c.ctx, 502, up.text[:200], {})
            raise _Fail(self._error_response(c, cls, err), up.status_code) from None
        if self.native:
            self._fix_native_message(c, data)
            out: dict[str, Any] = data
        else:
            out, failures = c.core.rewrite_response(c.ctx, data)
            if failures:
                body2, failures2 = await self._retry(c, wire, out, failures)
                if body2 is not None:
                    out, failures = body2, failures2
                if failures and self._resolve(c.ctx, failures):
                    err = c.core.unrepaired(c.ctx, failures)
                    raise _Fail(self._plain_error(c, 502, err["error"]["message"], "api_error"), up.status_code)
        mw = await c.core.after_response(c.ctx, out)
        if isinstance(mw, Reject):
            raise _Fail(self._reject(c, mw), up.status_code)
        msg = mw if self.native else tr.from_openai_response(mw)
        self._usage_fix(c, msg, feats)
        return msg

    def _resolve(self, ctx: RewriteContext, failures: list[ToolFailure]) -> bool:
        for f in failures:
            if f.kind == "schema":
                ctx.emit(
                    "tool-args-schema-mismatch",
                    "arguments parse",
                    "passed through unchanged after one retry",
                    details={"tool": f.name, "index": f.index, "issues": [f.parse_error]},
                )
        return any(f.kind == "parse" for f in failures)

    async def _retry(
        self, c: _Call, wire: dict[str, Any], out: dict[str, Any], failures: list[ToolFailure]
    ) -> tuple[dict[str, Any] | None, list[ToolFailure]]:
        msg = (out.get("choices") or [{}])[0].get("message") or {}
        plan = c.core.plan_retry(c.ctx, wire, msg, failures)
        if plan is None:
            return None, failures
        rewrite_reasoning_replay(c.ctx, plan.request)
        try:
            r2 = await self.client.post(
                self.url, content=json.dumps(plan.request, ensure_ascii=False).encode(), headers=c.headers
            )
            if r2.status_code >= 400:
                return None, failures
            return c.core.rewrite_response(c.ctx, r2.json())
        except (httpx.HTTPError, ValueError):
            return None, failures

    def _message_response(self, c: _Call, msg: dict[str, Any]) -> Response:
        c.usage = msg.get("usage")
        return JSONResponse(
            msg,
            headers={
                BRIDGE_ID_HEADER: c.ctx.request_id,
                "request-id": c.ctx.request_id,
                BRIDGE_FIXES_HEADER: self._fixes_header(c),
            },
        )

    # ------------------------------------------------------------------------------------ web_search emulation
    async def _emulated(self, c: _Call, sent: dict[str, Any]) -> dict[str, Any]:
        backend = self.opt.search_backend
        assert backend is not None
        max_uses = int(c.ctx.extras["web_search"]["max_uses"])
        body = {**sent, "stream": False}  # run the searches non-stream; a stream client gets a replay
        out_blocks: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        uses = 0
        msg: dict[str, Any] = {}
        for _ in range(max_uses + 2):
            msg = await self._complete(c, body)
            for k, v in (msg.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
            blocks = msg.get("content") or []
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            calls = [b for b in tool_uses if b.get("name") == ws.WEB_SEARCH_NAME]
            if not calls or len(calls) != len(tool_uses):
                out_blocks += blocks
                break
            out_blocks += [b for b in blocks if b.get("type") != "tool_use"]
            results: list[dict[str, Any]] = []
            for call in calls:
                sid = ws.server_tool_id(call["id"])
                out_blocks.append(
                    {"type": "server_tool_use", "id": sid, "name": ws.WEB_SEARCH_NAME, "input": call.get("input") or {}}
                )
                query = str((call.get("input") or {}).get("query") or "").strip()
                if uses >= max_uses:
                    out_blocks.append(ws.result_block(sid, None, error="max_uses_exceeded"))
                    text = "web_search error: max_uses_exceeded"
                elif not query:
                    out_blocks.append(ws.result_block(sid, None, error="invalid_tool_input"))
                    text = "web_search error: invalid_tool_input (query is required)"
                else:
                    uses += 1
                    try:
                        found = await backend.search(query, max_results=5)
                        out_blocks.append(ws.result_block(sid, found))
                        text = ws.format_results(found)
                    except Exception as e:  # a backend failure must not fail the whole call
                        out_blocks.append(ws.result_block(sid, None, error="unavailable"))
                        text = "web_search error: unavailable"
                        log.warning("%s web_search backend failed (%s)", c.ctx.request_id, type(e).__name__)
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": text})
            body = {
                **body,
                "messages": [
                    *body["messages"],
                    {"role": "assistant", "content": blocks},
                    {"role": "user", "content": results},
                ],
            }
        usage["server_tool_use"] = {"web_search_requests": uses}  # type: ignore[assignment]
        final = {**msg, "content": out_blocks, "usage": usage}
        c.ctx.emit(
            "anthropic-web-search-run",
            "model asked for web_search",
            f"{uses} search(es) run by the bridge backend",
            details={"searches": uses},
        )
        return final

    # ------------------------------------------------------------------------------------ stream
    async def _stream(self, c: _Call, sent: dict[str, Any]) -> Response:
        wire = await self._wire_request(c, sent)
        up = await self._post(c, wire, stream=True)
        gen = (
            self._native_stream(c, up, self._features(sent))
            if self.native
            else self._openai_stream(c, wire, up, self._features(sent))
        )
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                BRIDGE_ID_HEADER: c.ctx.request_id,
                "request-id": c.ctx.request_id,
                BRIDGE_FIXES_HEADER: self._fixes_header(c),
            },
        )

    def _stream_error(self, message: str, etype: str = "api_error") -> bytes:
        return tr.sse_event({"type": "error", "error": {"type": etype, "message": message}})

    async def _native_stream(self, c: _Call, up: httpx.Response, feats: Features) -> AsyncIterator[bytes]:
        parser = SSEParser()
        status = 200
        start_usage: dict[str, Any] = {}
        final_usage: dict[str, Any] = {}
        injected = False

        async def handle(ev: SSEEvent) -> AsyncIterator[bytes]:
            nonlocal start_usage, final_usage, injected
            try:
                obj = json.loads(ev.data)
            except ValueError:
                yield (f"event: {ev.event}\n" if ev.event else "").encode() + f"data: {ev.data}\n\n".encode()
                return
            if not isinstance(obj, dict):
                yield f"data: {ev.data}\n\n".encode()
                return
            kind = obj.get("type")
            if kind == "message_start":
                usage = (obj.get("message") or {}).setdefault("usage", {})
                start_usage = dict(usage)
                if tr.total_input(usage) == 0:
                    n = c.ctx.extras["est_input"]
                    usage["input_tokens"] = n
                    injected = True
                    c.ctx.emit(
                        "anthropic-usage-inject",
                        "message_start usage.input_tokens is 0",
                        f"message_start usage.input_tokens={n} (calibrated estimate, real usage in message_delta)",
                        details={"estimate": n},
                    )
            elif kind == "message_delta" and isinstance(obj.get("usage"), dict):
                final_usage = dict(obj["usage"])
            res = await c.core.on_stream_event(c.ctx, [obj])
            if isinstance(res, Reject):
                raise _StreamRejected(res)
            for e in res:
                yield tr.sse_event(e)

        try:
            async for text in up.aiter_text():
                for ev in parser.feed(text):
                    async for b in handle(ev):
                        yield b
            for ev in parser.close():
                async for b in handle(ev):
                    yield b
        except _StreamRejected as r:
            status = r.reject.status
            if r.reject.fix_id:
                c.ctx.emit(r.reject.fix_id, "stream", f"stream ended: {r.reject.message}", ok=False)
            yield self._stream_error(r.reject.message, r.reject.error_type)
        except httpx.HTTPError as e:
            status = 502
            yield self._stream_error(f"Upstream stream failed ({type(e).__name__}).")
        except asyncio.CancelledError:
            status = 499
            raise
        finally:
            await up.aclose()
            real = final_usage if tr.total_input(final_usage) else ({} if injected else start_usage)
            usage = {**start_usage, **final_usage}
            if injected and tr.total_input(final_usage):
                usage["input_tokens"] = final_usage.get("input_tokens", usage["input_tokens"])
            c.usage = usage or None
            if tr.total_input(real):
                self.opt.calibrator.observe(c.ctx.model, feats, tr.total_input(real))
            with contextlib.suppress(Exception):
                await c.core.on_finish(c.ctx, tr.openai_usage(c.usage))
            self._finish(c, status)

    async def _openai_stream(
        self, c: _Call, wire: dict[str, Any], up: httpx.Response, feats: Features
    ) -> AsyncIterator[bytes]:
        ctx, core = c.ctx, c.core
        parser = SSEParser()
        stream_rw = core.new_stream(ctx)
        start = {
            "input_tokens": ctx.extras["est_input"],
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        translator = tr.OpenAIStreamTranslator(model=ctx.model, start_usage=start)
        status = 200
        usage_est = ctx.extras["est_input"]
        ctx.emit(
            "anthropic-usage-inject",
            "OpenAI streams report usage last, message_start would carry none",
            f"message_start usage.input_tokens={usage_est} (calibrated estimate, real usage in message_delta)",
            details={"estimate": usage_est, "stream": True},
        )

        async def forward(events: list[Any]) -> AsyncIterator[bytes]:
            res = await core.on_stream_event(ctx, events)
            if isinstance(res, Reject):
                raise _StreamRejected(res)
            for e in res:
                for a in translator.feed(e):
                    yield tr.sse_event(a)

        async def handle_retry() -> AsyncIterator[bytes]:
            nonlocal status
            plan = core.plan_retry(ctx, wire, stream_rw.assistant_message, stream_rw.failures)
            body2: dict[str, Any] | None = None
            failures2 = stream_rw.failures
            if plan is not None:
                rewrite_reasoning_replay(ctx, plan.request)
                with contextlib.suppress(httpx.HTTPError, ValueError):
                    r2 = await self.client.post(
                        self.url, content=json.dumps(plan.request, ensure_ascii=False).encode(), headers=c.headers
                    )
                    if r2.status_code < 400:
                        body2, failures2 = core.rewrite_response(ctx, r2.json())
            if body2 is not None and not any(f.kind == "parse" for f in failures2):
                calls = ((body2.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
                self._resolve(ctx, failures2)
                async for b in forward(stream_rw.resolve(calls)):
                    yield b
                return
            if not self._resolve(ctx, failures2):
                async for b in forward(stream_rw.resolve(stream_rw.assistant_message["tool_calls"])):
                    yield b
                return
            err = core.unrepaired(ctx, failures2)
            status = 502
            stream_rw.fail()
            yield self._stream_error(err["error"]["message"])
            translator.finished = True  # no message_stop after an error event

        async def process(evs: list[SSEEvent]) -> AsyncIterator[bytes]:
            for ev in evs:
                data = ev.data.strip()
                if data == "[DONE]":
                    obj: Any = "[DONE]"
                else:
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and isinstance(obj.get("error"), dict):  # error in the stream
                        raise httpx.ReadError(str(obj["error"].get("message", "upstream error")))
                async for b in forward(stream_rw.feed(obj)):
                    yield b
                if stream_rw.needs_retry:
                    async for b in handle_retry():
                        yield b

        try:
            async for text in up.aiter_text():
                async for b in process(parser.feed(text)):
                    yield b
            async for b in process(parser.close()):
                yield b
            async for b in forward(stream_rw.close()):
                yield b
            if stream_rw.needs_retry:
                async for b in handle_retry():
                    yield b
            for a in translator.finish():
                yield tr.sse_event(a)
        except _StreamRejected as r:
            status = r.reject.status
            if r.reject.fix_id:
                ctx.emit(r.reject.fix_id, "stream", f"stream ended: {r.reject.message}", ok=False)
            yield self._stream_error(r.reject.message, r.reject.error_type)
        except httpx.HTTPError as e:
            status = 502
            yield self._stream_error(f"Upstream stream failed ({type(e).__name__}).")
        except asyncio.CancelledError:
            status = 499
            raise
        finally:
            await up.aclose()
            c.usage = (
                tr.anthropic_usage(translator.usage)
                if translator.usage
                else {"input_tokens": usage_est, "output_tokens": 0}
            )
            if translator.usage:
                self.opt.calibrator.observe(ctx.model, feats, int(translator.usage.get("prompt_tokens") or 0))
            with contextlib.suppress(Exception):
                await core.on_finish(ctx, translator.usage)
            self._finish(c, status)

    # ------------------------------------------------------------------------------------ handlers
    async def messages(self, request: Request) -> Response:
        try:
            body = json.loads(await request.body())
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except ValueError:
            return self._plain_error(None, 400, "Request body must be a JSON object.")
        c = self._call(request, body)
        status = 200
        try:
            sent = self._prepare(c, body)
            if c.ctx.extras.get("web_search"):
                msg = await self._emulated(c, sent)
                c.usage = msg.get("usage")
                if c.ctx.stream:
                    resp: Response = StreamingResponse(
                        _replay(msg),
                        media_type="text/event-stream",
                        headers={
                            "cache-control": "no-cache",
                            BRIDGE_ID_HEADER: c.ctx.request_id,
                            "request-id": c.ctx.request_id,
                        },
                    )
                else:
                    resp = self._message_response(c, msg)
                await c.core.on_finish(c.ctx, tr.openai_usage(msg.get("usage")))
            elif c.ctx.stream:
                return await self._stream(c, sent)  # the generator records the request when the stream ends
            else:
                msg = await self._complete(c, sent)
                resp = self._message_response(c, msg)
                await c.core.on_finish(c.ctx, tr.openai_usage(msg.get("usage")))
        except _Fail as f:
            resp, status = f.response, f.response.status_code
        self._finish(c, status)
        return resp

    async def count_tokens(self, request: Request) -> Response:
        try:
            body = json.loads(await request.body())
            if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
                raise ValueError("messages required")
        except ValueError:
            return self._plain_error(None, 400, "messages: Field required")
        c = self._call(request, body)
        feats = self._features(body)
        coef = self.opt.calibrator.coefficients(body.get("model"))
        n = self.opt.calibrator.estimate_features(body.get("model"), feats)
        c.ctx.emit(
            "anthropic-count-tokens",
            "upstream count_tokens not used",
            f"estimated {n} input tokens locally",
            details={
                "estimate": n,
                "content_tokens": round(feats[0]),
                "messages": feats[1],
                "coefficients": [round(v, 3) for v in coef],
                "observations": self.opt.calibrator.observations,
            },
        )
        c.usage = None
        self._finish(c, 200)
        return JSONResponse(
            {"input_tokens": n},
            headers={
                BRIDGE_ID_HEADER: c.ctx.request_id,
                "request-id": c.ctx.request_id,
                BRIDGE_FIXES_HEADER: "anthropic-count-tokens",
            },
        )


async def _replay(msg: dict[str, Any]) -> AsyncIterator[bytes]:
    for e in tr.events_from_message(msg):
        yield tr.sse_event(e)


def create_anthropic_app(
    cfg: ProxyConfig,
    *,
    options: AnthropicOptions | None = None,
    extra_routes: Sequence[BaseRoute] = (),
    on_build: Callable[[AnthropicBridge], None] | None = None,
    **proxy_kwargs: Any,
) -> Starlette:
    """The proxy app plus ``/v1/messages`` and ``/v1/messages/count_tokens`` (``proxy.create_app`` extra routes)."""
    client = proxy_kwargs.pop("http", None) or httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_s, connect=10.0))
    bridge = AnthropicBridge(cfg, options or AnthropicOptions(), client)
    app = create_app(cfg, extra_routes=[*bridge.routes(), *extra_routes], http=client, **proxy_kwargs)
    app.state.anthropic = bridge
    if on_build:
        on_build(bridge)
    return app
