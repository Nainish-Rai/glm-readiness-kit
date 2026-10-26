"""Fix events: one structured record per applied fix.

An event says *which* fix ran on *which* request and gives a before/after **summary** (short, no message
content). Raw before/after values are attached under ``content`` only when the emitter was created with
``log_content=True`` (``glmkit bridge serve --log-content``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# fix id -> (title, default outcome). Outcome "fail" means the bridge could not fix it.
FIX_TITLES: dict[str, str] = {
    "thinking-disabled-to-low": "thinking: disabled rewritten to enabled + effort low (forced-thinking model)",
    "effort-clamp": "reasoning_effort clamped to the values the model accepts",
    "schema-flatten": "top-level oneOf/anyOf tool schema flattened",
    "schema-args-unflatten": "arguments of a flattened tool mapped back to one branch of the original schema",
    "tool-args-repair": "malformed tool-call arguments repaired",
    "tool-args-retry": "upstream re-asked once with the parse error injected",
    "tool-args-unrepaired": "tool-call arguments could not be repaired (error returned, no {} substituted)",
    "tool-args-schema-mismatch": "tool-call arguments parse but violate the tool's schema",
    "reasoning-replay": "reasoning_content replayed the way the upstream expects",
    "reasoning-normalize": "upstream reasoning field renamed to reasoning_content for the client",
    "error-classify": "upstream error mapped to a standard HTTP status / retry hint",
    "error-sanitize": "non-JSON upstream error (possible internal leak) replaced by a clean error",
}
FAILED_FIXES = frozenset({"tool-args-unrepaired"})


@dataclass
class FixEvent:
    fix_id: str
    request_id: str
    before: str
    after: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    upstream: str | None = None
    details: dict[str, Any] = field(default_factory=dict)  # metadata only: tool name, index, parse error position
    content: dict[str, Any] | None = None  # raw before/after, only with log_content
    ok: bool = True

    @property
    def title(self) -> str:
        return FIX_TITLES.get(self.fix_id, self.fix_id)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "fix_id": self.fix_id,
            "request_id": self.request_id,
            "ts": self.ts,
            "upstream": self.upstream,
            "before": self.before,
            "after": self.after,
            "details": self.details,
            "ok": self.ok,
        }
        if self.content is not None:
            d["content"] = self.content
        return d


class FixEmitter:
    """Collects fix events and forwards them to optional sinks. Thread-safe."""

    def __init__(self, *, log_content: bool = False, sinks: list[Callable[[FixEvent], None]] | None = None) -> None:
        self.log_content = log_content
        self.sinks = list(sinks or [])
        self.events: list[FixEvent] = []
        self._lock = threading.Lock()

    def emit(
        self,
        fix_id: str,
        request_id: str,
        before: str,
        after: str,
        *,
        upstream: str | None = None,
        details: dict[str, Any] | None = None,
        content: dict[str, Any] | None = None,
        ok: bool | None = None,
    ) -> FixEvent:
        ev = FixEvent(
            fix_id=fix_id,
            request_id=request_id,
            before=before,
            after=after,
            upstream=upstream,
            details=dict(details or {}),
            content=content if self.log_content else None,
            ok=(fix_id not in FAILED_FIXES) if ok is None else ok,
        )
        with self._lock:
            self.events.append(ev)
        for sink in self.sinks:
            sink(ev)
        return ev

    def for_request(self, request_id: str) -> list[FixEvent]:
        with self._lock:
            return [e for e in self.events if e.request_id == request_id]
