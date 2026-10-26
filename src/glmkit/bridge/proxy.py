"""OpenAI-compatible bridge proxy (Starlette + httpx). Drives ``glmkit.bridge.core``; contains no fix logic.

Routes: ``POST .../chat/completions`` (any prefix), ``GET /healthz``, ``GET /_bridge/info``. More routes (the
Anthropic path of issue 07) are added through ``extra_routes``.

Logging: request id, path, status and fix ids only. Auth headers are never logged or recorded. Message
content is logged only with ``log_content=True``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from glmkit.bridge.core import BridgeCore, FixEmitter, Reject, RewriteContext, UpstreamProfile
from glmkit.bridge.core.errors import ClassifiedError
from glmkit.bridge.core.pipeline import rewrite_reasoning_replay
from glmkit.bridge.core.retry import ToolFailure
from glmkit.bridge.recorder import RequestRecord, SessionRecorder
from glmkit.client.sse import SSEParser
from glmkit.contracts.profile import EndpointProfile
from glmkit.redact import Redactor

log = logging.getLogger("glmkit.bridge")
FORWARD_HEADERS = ("accept", "user-agent")
BRIDGE_ID_HEADER = "x-glmkit-bridge-request-id"
BRIDGE_FIXES_HEADER = "x-glmkit-bridge-fixes"


@dataclass
class ProxyConfig:
    profile: EndpointProfile
    upstream: UpstreamProfile
    out_dir: Path = Path("results")
    log_content: bool = False
    timeout_s: float = 120.0
    flush_interval_s: float = 1.0
    middleware: list[Any] = field(default_factory=list)


def _error(
    status: int, message: str, code: str, etype: str = "api_error", headers: dict[str, str] | None = None
) -> Response:
    return JSONResponse(
        {"error": {"message": message, "type": etype, "code": code}}, status_code=status, headers=headers
    )


def sse(obj: Any) -> bytes:
    data = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n".encode()


def create_app(
    cfg: ProxyConfig,
    *,
    core: BridgeCore | None = None,
    recorder: SessionRecorder | None = None,
    extra_routes: Sequence[BaseRoute] = (),
    http: httpx.AsyncClient | None = None,
) -> Starlette:
    key = cfg.profile.api_key()
    redactor = Redactor([key])
    recorder = recorder or SessionRecorder(
        cfg.profile,
        cfg.out_dir,
        flavor=cfg.upstream.name,
        config={"log_content": cfg.log_content, "upstream_profile": cfg.profile.name},
        redactor=redactor,
    )
    emitter = FixEmitter(log_content=cfg.log_content, sinks=[recorder.on_event])
    core = core or BridgeCore(cfg.upstream, emitter=emitter, middleware=list(cfg.middleware))
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_s, connect=10.0))
    url = cfg.profile.url

    def upstream_headers(request: Request) -> dict[str, str]:
        h = {"content-type": "application/json"}
        for name in FORWARD_HEADERS:
            if name in request.headers:
                h[name] = request.headers[name]
        for name, value in request.headers.items():  # X-GLMKit-* steer the mock server
            if name.lower().startswith("x-glmkit-") and not name.lower().startswith("x-glmkit-bridge"):
                h[name] = value
        h.update(cfg.profile.headers)
        if key:
            if cfg.profile.auth_style == "bearer":
                h["authorization"] = f"Bearer {key}"
            else:
                h["x-api-key"] = key
        else:  # no upstream key configured: pass the caller's credentials through, untouched and unlogged
            for name in ("authorization", "x-api-key"):
                if name in request.headers:
                    h[name] = request.headers[name]
        return h

    def trace(ctx: RewriteContext, label: str, payload: Any) -> None:
        if cfg.log_content:
            log.info("%s %s %s", ctx.request_id, label, redactor.text(json.dumps(payload, ensure_ascii=False)))

    def finish_record(ctx: RewriteContext, rec: RequestRecord, t0: float, status: int, up_status: int | None) -> None:
        rec.status, rec.upstream_status = status, up_status
        rec.duration_ms = round((time.monotonic() - t0) * 1000, 1)
        rec.retried = ctx.retries > 0
        rec.fixes = [e.fix_id for e in emitter.for_request(ctx.request_id)]
        recorder.add_request(rec)
        log.info("%s %s -> %s fixes=%s", ctx.request_id, rec.path, status, ",".join(rec.fixes) or "-")

    async def upstream_error(ctx: RewriteContext, resp: httpx.Response) -> Response:
        text = resp.text
        cls, body = core.classify_error(ctx, resp.status_code, text, dict(resp.headers))
        cls = await core.on_error(ctx, cls)
        return error_response(ctx, cls, body)

    def error_response(ctx: RewriteContext, cls: ClassifiedError, body: dict[str, Any]) -> Response:
        headers = {"x-should-retry": "true" if cls.retryable else "false", BRIDGE_ID_HEADER: ctx.request_id}
        if cls.retry_after_s is not None:
            headers["retry-after"] = str(cls.retry_after_s)
        return JSONResponse(body, status_code=cls.status, headers=headers)

    def reject_response(ctx: RewriteContext, rej: Reject) -> Response:
        if rej.fix_id:
            ctx.emit(
                rej.fix_id, "request", f"rejected with HTTP {rej.status}", details={"message": rej.message}, ok=False
            )
        headers = {BRIDGE_ID_HEADER: ctx.request_id}
        if rej.retry_after_s is not None:
            headers["retry-after"] = str(rej.retry_after_s)
        return JSONResponse(rej.body(), status_code=rej.status, headers=headers)

    def resolve_failures(ctx: RewriteContext, failures: list[ToolFailure]) -> bool:
        """After the one allowed retry. Emits events; True when a parse failure remains (error to client)."""
        parse = [f for f in failures if f.kind == "parse"]
        for f in failures:
            if f.kind == "schema":
                ctx.emit(
                    "tool-args-schema-mismatch",
                    "arguments parse",
                    "passed through unchanged after one retry",
                    details={"tool": f.name, "index": f.index, "issues": [f.parse_error]},
                )
        return bool(parse)

    async def post_json(body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        return await client.post(url, content=json.dumps(body, ensure_ascii=False).encode(), headers=headers)

    async def retry_nonstream(
        ctx: RewriteContext,
        sent: dict[str, Any],
        resp_body: dict[str, Any],
        failures: list[ToolFailure],
        headers: dict[str, str],
    ) -> tuple[dict[str, Any] | None, list[ToolFailure]]:
        """One retry with the parse error injected. Returns (new body or None, remaining failures)."""
        msg = (resp_body.get("choices") or [{}])[0].get("message") or {}
        plan = core.plan_retry(ctx, sent, msg, failures)
        if plan is None:
            return None, failures
        rewrite_reasoning_replay(ctx, plan.request)
        trace(ctx, "retry-request", plan.request)
        r2 = await post_json(plan.request, headers)
        if r2.status_code >= 400:
            return None, failures
        try:
            body2, failures2 = core.rewrite_response(ctx, r2.json())
        except ValueError:
            return None, failures
        return body2, failures2

    async def chat(request: Request) -> Response:
        t0 = time.monotonic()
        raw = await request.body()
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except ValueError:
            return _error(400, "Request body must be a JSON object.", "bridge_bad_request", "invalid_request_error")
        ctx = core.new_context(body)
        rec = RequestRecord(
            request_id=ctx.request_id,
            ts=datetime.now(UTC).isoformat(),
            path=request.url.path,
            stream=ctx.stream,
            model=ctx.model,
        )
        sent = core.rewrite_request(ctx, body)
        mw = await core.before_request(ctx, sent)
        if isinstance(mw, Reject):
            resp = reject_response(ctx, mw)
            finish_record(ctx, rec, t0, mw.status, None)
            return resp
        sent = mw
        trace(ctx, "request", sent)
        headers = upstream_headers(request)
        fix_header = ",".join(dict.fromkeys(e.fix_id for e in emitter.for_request(ctx.request_id)))
        try:
            if ctx.stream:
                req = client.build_request(
                    "POST", url, content=json.dumps(sent, ensure_ascii=False).encode(), headers=headers
                )
                up = await client.send(req, stream=True)
            else:
                up = await post_json(sent, headers)
        except httpx.TimeoutException:
            finish_record(ctx, rec, t0, 504, None)
            return _error(504, "Upstream timed out.", "bridge_upstream_timeout")
        except httpx.HTTPError as e:
            finish_record(ctx, rec, t0, 502, None)
            return _error(502, f"Upstream connection failed ({type(e).__name__}).", "bridge_upstream_unreachable")

        if up.status_code >= 400:
            if ctx.stream:
                await up.aread()
                await up.aclose()
            resp = await upstream_error(ctx, up)
            finish_record(ctx, rec, t0, resp.status_code, up.status_code)
            return resp

        if ctx.stream:
            return StreamingResponse(
                stream_body(ctx, rec, t0, sent, up, headers),
                media_type="text/event-stream",
                headers={
                    "cache-control": "no-cache",
                    BRIDGE_ID_HEADER: ctx.request_id,
                    BRIDGE_FIXES_HEADER: fix_header,
                },
            )

        try:
            data = up.json()
        except ValueError:
            cls, err = core.classify_error(ctx, 502, up.text[:200], {})
            finish_record(ctx, rec, t0, 502, up.status_code)
            return error_response(ctx, cls, err)
        out, failures = core.rewrite_response(ctx, data)
        if failures:
            body2, failures2 = await retry_nonstream(ctx, sent, out, failures, headers)
            if body2 is not None:
                out, failures = body2, failures2
            if failures and resolve_failures(ctx, failures):
                err = core.unrepaired(ctx, failures)
                finish_record(ctx, rec, t0, 502, up.status_code)
                return JSONResponse(
                    err, status_code=502, headers={BRIDGE_ID_HEADER: ctx.request_id, "x-should-retry": "false"}
                )
        mwr = await core.after_response(ctx, out)
        if isinstance(mwr, Reject):
            resp = reject_response(ctx, mwr)
            finish_record(ctx, rec, t0, mwr.status, up.status_code)
            return resp
        trace(ctx, "response", mwr)
        usage = mwr.get("usage") if isinstance(mwr.get("usage"), dict) else None
        rec.usage = usage
        await core.on_finish(ctx, usage)
        fixes = ",".join(dict.fromkeys(e.fix_id for e in emitter.for_request(ctx.request_id)))
        finish_record(ctx, rec, t0, 200, up.status_code)
        return JSONResponse(mwr, headers={BRIDGE_ID_HEADER: ctx.request_id, BRIDGE_FIXES_HEADER: fixes})

    async def stream_body(
        ctx: RewriteContext,
        rec: RequestRecord,
        t0: float,
        sent: dict[str, Any],
        up: httpx.Response,
        headers: dict[str, str],
    ) -> AsyncIterator[bytes]:
        parser = SSEParser()
        rw = core.new_stream(ctx)
        status = 200
        sent_done = False

        def mark(o: Any) -> bytes:
            nonlocal sent_done
            sent_done = sent_done or o == "[DONE]"
            return sse(o)

        try:

            async def forward(events: list[Any]) -> AsyncIterator[bytes]:
                res = await core.on_stream_event(ctx, events)
                if isinstance(res, Reject):
                    raise _StreamRejected(res)
                for e in res:
                    yield mark(e)

            async def handle_retry() -> AsyncIterator[bytes]:
                nonlocal status
                plan = core.plan_retry(ctx, sent, rw.assistant_message, rw.failures)
                body2: dict[str, Any] | None = None
                failures2 = rw.failures
                if plan is not None:
                    rewrite_reasoning_replay(ctx, plan.request)
                    trace(ctx, "retry-request", plan.request)
                    r2 = await post_json(plan.request, headers)
                    if r2.status_code < 400:
                        with contextlib.suppress(ValueError):
                            body2, failures2 = core.rewrite_response(ctx, r2.json())
                if body2 is not None and not any(f.kind == "parse" for f in failures2):
                    calls = ((body2.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
                    resolve_failures(ctx, failures2)
                    async for b in forward(rw.resolve(calls)):
                        yield b
                    return
                if not resolve_failures(ctx, failures2):  # only schema issues: pass the original calls through
                    async for b in forward(rw.resolve(rw.assistant_message["tool_calls"])):
                        yield b
                    return
                err = core.unrepaired(ctx, failures2)
                status = 502
                rw.fail()
                yield mark(err)
                yield mark("[DONE]")

            async def process(evs: list[Any]) -> AsyncIterator[bytes]:
                for ev in evs:
                    data = ev.data.strip()
                    if data == "[DONE]":
                        obj: Any = "[DONE]"
                    else:
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            yield f"data: {ev.data}\n\n".encode()
                            continue
                    out = rw.feed(obj)
                    async for b in forward(out):
                        yield b
                    if rw.needs_retry:
                        async for b in handle_retry():
                            yield b

            async for text in up.aiter_text():
                async for b in process(parser.feed(text)):
                    yield b
            async for b in process(parser.close()):
                yield b
            tail = rw.close()
            async for b in forward(tail):
                yield b
            if rw.needs_retry:
                async for b in handle_retry():
                    yield b
            if not sent_done:
                yield mark("[DONE]")
        except _StreamRejected as r:
            status = r.reject.status
            if r.reject.fix_id:
                ctx.emit(r.reject.fix_id, "stream", f"stream ended: {r.reject.message}", ok=False)
            yield mark(r.reject.body())
            yield mark("[DONE]")
        except httpx.HTTPError as e:
            status = 502
            yield mark(
                {
                    "error": {
                        "message": f"Upstream stream failed ({type(e).__name__}).",
                        "type": "api_error",
                        "code": "bridge_upstream_stream_error",
                    }
                }
            )
            yield mark("[DONE]")
        except asyncio.CancelledError:
            status = 499
            raise
        finally:
            await up.aclose()
            rec.usage = rw.usage
            with contextlib.suppress(Exception):
                await core.on_finish(ctx, rw.usage)
            finish_record(ctx, rec, t0, status, up.status_code)

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def info(_: Request) -> Response:
        return JSONResponse(
            {
                "upstream_profile": cfg.profile.name,
                "flavor": cfg.upstream.name,
                "run_id": recorder.env.run_id,
                "fix_events": len(emitter.events),
                "requests": len(recorder.requests),
                "log_content": cfg.log_content,
            }
        )

    flusher: asyncio.Task[None] | None = None

    async def flush_loop() -> None:
        while True:
            await asyncio.sleep(cfg.flush_interval_s)
            recorder.flush()

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        nonlocal flusher
        flusher = asyncio.create_task(flush_loop())
        try:
            yield
        finally:
            flusher.cancel()
            recorder.flush(final=True)
            await client.aclose()

    app = Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/_bridge/info", info, methods=["GET"]),
            *extra_routes,
            Route("/chat/completions", chat, methods=["POST"]),
            Route("/{prefix:path}/chat/completions", chat, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    app.state.core = core
    app.state.recorder = recorder
    app.state.emitter = emitter
    return app


class _StreamRejected(Exception):
    def __init__(self, reject: Reject) -> None:
        self.reject = reject
