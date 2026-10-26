"""Helpers shared by the wire-contract defects (slice 03). The leading underscore keeps this file out of defect
discovery."""

from __future__ import annotations

from starlette.responses import JSONResponse, PlainTextResponse, Response

from glmkit.mock import encode, engine
from glmkit.mock.types import MockContext

PY_TRACEBACK = """Traceback (most recent call last):
  File "/srv/gateway/handlers/{handler}.py", line {line}, in {func}
    {code}
{exc}
"""


def crash(handler: str, func: str, code: str, exc: str, line: int = 118) -> Response:
    """A 500 whose body is a raw Python traceback (the family of zai-coding-plugins#15)."""
    body = PY_TRACEBACK.format(handler=handler, func=func, code=code, exc=exc, line=line)
    return PlainTextResponse(body, status_code=500)


def answer_anyway(ctx: MockContext, *, model: str | None = None, echo_model: bool = False) -> Response | None:
    """Turn a rejected request into a normal 200 reply, as a host that swallows validation errors does.
    ``model`` replaces the requested model (silent fallback); ``echo_model`` keeps the requested name in the
    reply. Streaming requests are not supported (returns None, so the original error stands)."""
    req = ctx.req
    if req is None or req.stream:
        return None
    asked = req.model
    if model:
        req.model = model
    ctx.thinking = True
    ctx.effort = ctx.effort or "max"
    reply = engine.generate(ctx)
    reply.model = asked if echo_model else (model or asked or "")
    return JSONResponse(encode.encode_body(ctx, reply))
