"""Named defects for the mock GLM server.

A defect reproduces one known upstream bug. Each lives in its own module in this folder (or in an external
plugin module, see ``glmkit.plugins``) and is registered with ``@register_defect``. Defects are switched on
per server (``glmkit mock serve --defect NAME``, ``MockConfig.defects``) or per request
(``X-GLMKit-Defects: name1,name2``; ``none`` turns the server-wide ones off for that request).

Hooks run in this order; each defaults to a no-op:

1. ``on_request(ctx)``          -> return a Starlette ``Response`` to short-circuit everything
2. ``on_error(ctx, err)``       -> spec-mandated error (e.g. 400/1210); return a changed ``ErrorReply`` or a
                                   ``Response``
3. ``on_reply(ctx, reply)``     -> structured reply before encoding (content, tool calls, usage ...)
4. ``on_body(ctx, body)``       -> non-stream JSON body after encoding
5. ``on_events(ctx, events)``   -> streamed ``MockEvent`` list after encoding (set ``delay_s`` to stall)
"""

from __future__ import annotations

from typing import Any, ClassVar

from glmkit.mock.types import ErrorReply, MockContext, MockEvent, Reply
from glmkit.plugins import import_submodules, load_plugins

DEFECT_HEADER = "x-glmkit-defects"


class Defect:
    name: ClassVar[str]
    title: ClassVar[str] = ""
    sources: ClassVar[tuple[str, ...]] = ()  # upstream issue / doc URLs
    cases: ClassVar[tuple[str, ...]] = ()  # conformance case ids expected to catch this defect

    def on_request(self, ctx: MockContext) -> Any | None:
        return None

    def on_error(self, ctx: MockContext, err: ErrorReply) -> ErrorReply | Any:
        return err

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        return reply

    def on_body(self, ctx: MockContext, body: dict[str, Any]) -> dict[str, Any]:
        return body

    def on_events(self, ctx: MockContext, events: list[MockEvent]) -> list[MockEvent]:
        return events

    def state(self, ctx: MockContext) -> dict[str, Any]:
        """Per-server mutable state for this defect (e.g. a turn counter)."""
        return ctx.state.defect_state.setdefault(self.name, {})


_REGISTRY: dict[str, type[Defect]] = {}
_discovered = False


def register_defect(cls: type[Defect]) -> type[Defect]:
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} needs a 'name'")
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"defect {cls.name!r} registered twice")
    _REGISTRY[cls.name] = cls
    return cls


def discover() -> dict[str, type[Defect]]:
    """Import every module in this package and every plugin module, then return the registry."""
    global _discovered
    if not _discovered:
        import_submodules(__name__)
        _discovered = True
    load_plugins()
    return dict(sorted(_REGISTRY.items()))


def get_defect(name: str) -> Defect:
    reg = discover()
    if name not in reg:
        raise KeyError(f"unknown defect {name!r}; known: {', '.join(reg) or '(none)'}")
    return reg[name]()


def parse_names(value: str | None) -> list[str]:
    return [n.strip() for n in (value or "").split(",") if n.strip()]
