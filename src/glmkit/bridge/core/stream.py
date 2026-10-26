"""Stream rewriter: parsed OpenAI chat-completion chunks in, chunks to forward out.

* Text, reasoning and other events pass through immediately, in their original order.
* Tool-call deltas are held back per ``index`` (argument fragments are concatenated per index, however the
  upstream split them or interleaved the indices). At the chunk that carries ``finish_reason`` each held call is
  repaired once and emitted as ONE head+arguments delta, just before that finish chunk.
* If a call cannot be repaired the finish chunk and everything after it are held, ``needs_retry`` becomes true
  and the host decides: ``resolve(calls)`` with fixed calls (after a retry), or ``fail(error_body)``.

This costs streaming latency for tool-call arguments (research/02-bridge.md 3.4 notes the same for any gateway
that repairs arguments); text and reasoning still stream live.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from glmkit.bridge.core.retry import ToolFailure
from glmkit.bridge.core.toolcalls import fix_call, normalize_reasoning_keys

if TYPE_CHECKING:
    from glmkit.bridge.core.pipeline import RewriteContext

DONE = "[DONE]"
Event = dict[str, Any] | str


@dataclass
class _Call:
    id: str | None = None
    type: str = "function"
    name: str = ""
    parts: list[str] = field(default_factory=list)


class StreamRewriter:
    def __init__(self, ctx: RewriteContext) -> None:
        self.ctx = ctx
        self.calls: dict[int, _Call] = {}
        self._base: dict[str, Any] | None = None  # last chunk seen, as a template for synthesized chunks
        self._choice_index = 0
        self._last_index = -1
        self._ids: dict[str, int] = {}
        self._renamed = False
        self._flushed = False
        self.failures: list[ToolFailure] = []
        self.held: list[Event] = []
        self.assistant_message: dict[str, Any] = {}
        self.usage: dict[str, Any] | None = None
        self._text: list[str] = []

    @property
    def needs_retry(self) -> bool:
        return bool(self.failures)

    # -- feeding
    def feed(self, event: Event) -> list[Event]:
        if self.held or self.failures:  # a failed call is waiting for the host: queue everything behind it
            self.held.append(event)
            return []
        if isinstance(event, str):  # "[DONE]" (or any non-JSON data)
            flushed = self._flush()
            if self.failures:
                self.held.append(event)
                return []
            return [*flushed, event]
        if not isinstance(event, dict):
            return [event]
        if isinstance(event.get("usage"), dict):
            self.usage = event["usage"]
        self._base = {k: v for k, v in event.items() if k not in ("choices", "usage")}
        choices = event.get("choices") or []
        if not choices:
            return [event]
        ch = choices[0]
        self._choice_index = ch.get("index", 0)
        delta = ch.get("delta")
        had_tool_calls = False
        if isinstance(delta, dict):
            if normalize_reasoning_keys(self.ctx, delta):
                self._renamed = True
            if delta.get("content"):
                self._text.append(delta["content"])
            tcs = delta.pop("tool_calls", None)
            if tcs:
                had_tool_calls = True
                for tc in tcs:
                    self._absorb(tc)
        if ch.get("finish_reason") is not None:
            flushed = self._flush()
            if self.failures:
                self.held.append(event)
                return []
            return [*flushed, event]
        if had_tool_calls and not delta:
            return []  # the chunk only carried tool-call fragments
        return [event]

    def _absorb(self, tc: dict[str, Any]) -> None:
        idx = tc.get("index")
        tid = tc.get("id")
        if idx is None:
            if tid and tid in self._ids:
                idx = self._ids[tid]
            elif tid:
                idx = self._last_index + 1
            else:
                idx = max(self._last_index, 0)
        if tid:
            self._ids[tid] = idx
        self._last_index = max(self._last_index, idx)
        call = self.calls.setdefault(idx, _Call())
        if tid:
            call.id = tid
        if tc.get("type"):
            call.type = tc["type"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            call.name = fn["name"] if not call.name else call.name
        if fn.get("arguments"):
            call.parts.append(fn["arguments"])

    # -- flushing
    def _flush(self) -> list[Event]:
        """Repair the held calls. On any failure nothing is emitted and ``self.failures`` is filled."""
        if self._flushed or not self.calls:
            if self._renamed:
                self._announce_rename()
            return []
        out: list[Event] = []
        fixed: dict[int, str] = {}
        failures: list[ToolFailure] = []
        for idx in sorted(self.calls):
            c = self.calls[idx]
            res = fix_call(self.ctx, c.name, "".join(c.parts), index=idx, call_id=c.id)
            if res.failure:
                failures.append(res.failure)
            else:
                fixed[idx] = res.arguments
        self._flushed = True
        self.assistant_message = {
            "role": "assistant",
            "content": "".join(self._text) or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": c.type,
                    "function": {"name": c.name, "arguments": fixed.get(i, "".join(c.parts))},
                }
                for i, c in sorted(self.calls.items())
            ],
        }
        if failures:
            self.failures = failures
            return []
        if self._renamed:
            self._announce_rename()
        for idx in sorted(self.calls):
            c = self.calls[idx]
            out.append(self._chunk([self._tc(idx, c.id, c.type, c.name, fixed[idx])]))
        return out

    def _announce_rename(self) -> None:
        self._renamed = False
        self.ctx.emit("reasoning-normalize", "reasoning", "reasoning_content")

    def _tc(self, idx: int, tid: str | None, typ: str, name: str, args: str) -> dict[str, Any]:
        tc: dict[str, Any] = {"index": idx, "function": {"name": name, "arguments": args}}
        if tid:
            tc["id"] = tid
        tc["type"] = typ
        return tc

    def _chunk(self, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        base = copy.deepcopy(self._base or {})
        base.setdefault("object", "chat.completion.chunk")
        base["choices"] = [{"index": self._choice_index, "delta": {"tool_calls": tool_calls}, "finish_reason": None}]
        return base

    def close(self) -> list[Event]:
        """The upstream stream ended without a finish chunk: flush what is held."""
        out = self._flush()
        return out

    # -- host decisions after a failure
    def resolve(self, tool_calls: list[dict[str, Any]]) -> list[Event]:
        """Emit ``tool_calls`` (OpenAI message form, already fixed) and release the held events."""
        out: list[Event] = []
        for i, tc in enumerate(tool_calls):
            fn = tc.get("function") or {}
            out.append(
                self._chunk(
                    [self._tc(i, tc.get("id"), tc.get("type", "function"), fn.get("name", ""), fn.get("arguments", ""))]
                )
            )
        held, self.held, self.failures = self.held, [], []
        return out + held

    def fail(self) -> list[Event]:
        """Drop the held events (the host replaces them with an error event)."""
        self.held, self.failures = [], []
        return []
