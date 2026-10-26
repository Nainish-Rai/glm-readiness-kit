"""Middleware hook: how the guard (issue 09) and other policies plug into the core.

A middleware is any object with some of the methods below (all optional; subclass ``BridgeMiddleware`` for
no-op defaults). Methods may be plain or ``async``; the host awaits what it gets. They run in list order for
``before_request`` / ``on_stream_event`` / ``after_response`` and in reverse order for ``on_error``.

* ``before_request(ctx, body)``  -> a (possibly changed) body, or ``Reject(...)`` to stop with an error response.
  Runs *after* the request rewriters, so it sees exactly what would be sent upstream.
* ``after_response(ctx, body)``  -> a (possibly changed) non-stream response body, or ``Reject``.
  Runs after the response rewriters.
* ``on_stream_event(ctx, event)`` -> a list of events to forward (return ``[event]`` to pass through, ``[]`` to
  swallow) or ``Reject`` to end the stream with an error event. Sees events after the stream rewriter.
* ``on_error(ctx, classified)``  -> optionally a changed ``ClassifiedError``.
* ``on_finish(ctx, usage)``       -> called once per client call with the final ``usage`` dict (or None); the
  place for spend accounting.

State that must survive between calls (budgets, loop counters) lives on the middleware instance; per-call
state goes on ``ctx.extras``.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Reject:
    status: int = 429
    message: str = "Request rejected by bridge middleware"
    code: str | None = None
    error_type: str = "rate_limit_error"
    retry_after_s: int | None = None
    fix_id: str | None = None  # optional: also emit a fix event under this id
    extra: dict[str, Any] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"message": self.message, "type": self.error_type, "code": self.code}
        err.update(self.extra)
        return {"error": err}


class BridgeMiddleware:
    def before_request(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any] | Reject:
        return body

    def after_response(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any] | Reject:
        return body

    def on_stream_event(self, ctx: Any, event: Any) -> list[Any] | Reject:
        return [event]

    def on_error(self, ctx: Any, classified: Any) -> Any:
        return classified

    def on_finish(self, ctx: Any, usage: dict[str, Any] | None) -> None:
        return None


async def call_hook(mw: Any, name: str, *args: Any) -> Any:
    """Call ``mw.<name>(*args)`` if it exists; await it if it is a coroutine. Returns None when absent."""
    fn = getattr(mw, name, None)
    if fn is None:
        return None
    out = fn(*args)
    return await out if inspect.isawaitable(out) else out
