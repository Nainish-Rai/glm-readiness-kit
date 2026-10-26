"""A host that echoes request headers (including the API key) into its error and reply bodies. Used to
prove the kit's redaction: the key must never reach results, traces or logs even when upstream leaks it."""

from __future__ import annotations

from typing import Any

from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import ErrorReply, MockContext, Reply


def _leak(ctx: MockContext) -> str:
    return ctx.headers.get("authorization") or ctx.headers.get("x-api-key") or "(no auth header)"


@register_defect
class AuthEcho(Defect):
    name = "auth-echo"
    title = "echoes the caller's Authorization / x-api-key header in response bodies"

    def on_error(self, ctx: MockContext, err: ErrorReply) -> ErrorReply:
        err.message = f"{err.message} (debug: auth={_leak(ctx)})"
        return err

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        reply.content = f"{reply.content or ''} [debug auth={_leak(ctx)}]"
        return reply

    def on_body(self, ctx: MockContext, body: dict[str, Any]) -> dict[str, Any]:
        return {**body, "debug_headers": {"auth": _leak(ctx)}}
