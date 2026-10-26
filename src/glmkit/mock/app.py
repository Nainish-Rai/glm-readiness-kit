"""ASGI app of the mock GLM server.

Routes (any prefix is accepted, so Z.ai base URLs work unchanged):
  POST .../chat/completions          OpenAI-compatible (``/api/paas/v4``, ``/api/coding/paas/v4`` ...)
  POST .../v1/messages               Anthropic-compatible (``/api/anthropic/v1/messages``)
  POST .../v1/messages/count_tokens  Anthropic token count
  GET  /_mock/info                   active config and available defects
  GET  /health

A path containing ``/coding/`` is served as the Coding Plan endpoint class; others use
``MockConfig.endpoint_class`` (default ``api``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from glmkit.mock import defects as defect_registry
from glmkit.mock import encode, engine
from glmkit.mock.types import ErrorReply, MockConfig, MockContext, ServerState


def _protocol(path: str) -> str | None:
    p = path.rstrip("/")
    if p.endswith("/chat/completions"):
        return "openai_chat"
    if p.endswith("/v1/messages/count_tokens"):
        return "anthropic_count_tokens"
    if p.endswith("/v1/messages"):
        return "anthropic_messages"
    return None


def _error_response(ctx: MockContext, err: ErrorReply) -> Response:
    return JSONResponse(encode.error_body(ctx, err), status_code=err.status)


def _mock_error(message: str, status: int = 400) -> Response:
    return JSONResponse(
        {"error": {"code": "mock", "message": message}}, status_code=status, headers={"x-glmkit-mock-error": "1"}
    )


def create_app(config: MockConfig | None = None) -> Starlette:
    config = config or MockConfig()
    available = defect_registry.discover()
    for name in config.defects:
        if name not in available:
            raise KeyError(f"unknown defect {name!r}; known: {', '.join(available)}")
    state = ServerState()

    async def info(_: Request) -> Response:
        return JSONResponse(
            {
                "endpoint_class": config.endpoint_class,
                "defects_active": config.defects,
                "defects_available": [
                    {"name": n, "title": c.title, "sources": list(c.sources), "cases": list(c.cases)}
                    for n, c in defect_registry.discover().items()
                ],
                "rules": [r.get("name") for r in config.rules],
                "requests": state.requests,
            }
        )

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def handle(request: Request) -> Response:
        path = request.url.path
        protocol = _protocol(path)
        if protocol is None:
            return _mock_error(f"no mock route for {path}", 404)
        raw = await request.body()
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = None
        headers = {k.lower(): v for k, v in request.headers.items()}
        names = defect_registry.parse_names(headers.get(defect_registry.DEFECT_HEADER))
        active = [] if "none" in names else list(config.defects)
        active += [n for n in names if n != "none" and n not in active]
        try:
            defects = [defect_registry.get_defect(n) for n in active]
        except KeyError as e:
            return _mock_error(str(e.args[0]))
        state.requests += 1
        ctx = MockContext(
            protocol="anthropic_messages" if protocol == "anthropic_count_tokens" else protocol,
            path=path,
            endpoint_class="plan" if "/coding/" in path else config.endpoint_class,
            headers=headers,
            body=body,
            raw=raw,
            config=config,
            state=state,
            defects=defects,
        )
        for d in defects:
            short = d.on_request(ctx)
            if short is not None:
                return short
        err = engine.check_auth(ctx)
        if err is None and protocol == "anthropic_count_tokens":
            if not isinstance(body, dict):
                err = ErrorReply(
                    400, "1210", "Invalid API parameter, please check the documentation.", reason="bad_json"
                )
            else:
                ctx.req = engine.parse_request("anthropic_messages", body)
                prompt, _ = engine.prompt_usage(ctx)
                return JSONResponse({"input_tokens": prompt})
        err = err or engine.validate(ctx)
        if err is None:
            ctx.rule = engine.match_rule(ctx)
            if ctx.rule:
                err = engine.rule_error(ctx.rule)
        if err is not None:
            out: Any = err
            for d in defects:
                out = d.on_error(ctx, out)
                if isinstance(out, Response):
                    return out
            return _error_response(ctx, out)

        reply = engine.rule_reply(ctx, ctx.rule) if ctx.rule else engine.generate(ctx)
        for d in defects:
            reply = d.on_reply(ctx, reply)
        delay = float((ctx.rule or {}).get("delay_s", config.first_token_delay_s))
        assert ctx.req is not None
        if ctx.req.stream:
            events = encode.encode_events(ctx, reply)
            for d in defects:
                events = d.on_events(ctx, events)
            chunk_delay = float((ctx.rule or {}).get("chunk_delay_s", config.chunk_delay_s))

            async def gen() -> AsyncIterator[bytes]:
                if delay:
                    await asyncio.sleep(delay)
                for i, e in enumerate(events):
                    wait = e.delay_s + (chunk_delay if i else 0.0)
                    if wait:
                        await asyncio.sleep(wait)
                    yield encode.sse_bytes(e)

            return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache"})
        body_out = encode.encode_body(ctx, reply)
        for d in defects:
            body_out = d.on_body(ctx, body_out)
        if delay:
            await asyncio.sleep(delay)
        return JSONResponse(body_out)

    return Starlette(
        routes=[
            Route("/_mock/info", info, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", handle, methods=["POST"]),
        ]
    )
