"""Retry policy interface: lets the host re-issue a call with something injected.

Only one policy ships: ``ParseErrorRetry`` re-asks the model once, with the tool-argument parse error shown to it
as the tool result. A host (proxy, LiteLLM plugin) calls ``plan_after_tool_failure`` after a response whose tool
arguments could not be repaired and re-sends ``RetryPlan.request`` when a plan comes back.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

RETRY_MARKER = "Tool call arguments were not valid"


@dataclass
class ToolFailure:
    index: int
    call_id: str | None
    name: str
    arguments: str
    parse_error: str
    kind: str = "parse"  # parse: not JSON / not an object; schema: valid JSON that misses required fields


@dataclass
class RetryPlan:
    request: dict[str, Any]  # the full request body to re-send (stream flag untouched)
    delay_s: float = 0.0
    reason: str = ""


@runtime_checkable
class RetryPolicy(Protocol):
    def plan_after_tool_failure(
        self,
        request: dict[str, Any],
        assistant_message: dict[str, Any],
        failures: list[ToolFailure],
        attempts_so_far: int,
    ) -> RetryPlan | None: ...


class ParseErrorRetry:
    """At most ``max_retries`` (default 1) per client call. Injects the failed assistant turn plus one ``tool``
    message per failed call carrying the parse error, so the model re-emits the call."""

    def __init__(self, max_retries: int = 1) -> None:
        self.max_retries = max_retries

    def plan_after_tool_failure(
        self,
        request: dict[str, Any],
        assistant_message: dict[str, Any],
        failures: list[ToolFailure],
        attempts_so_far: int,
    ) -> RetryPlan | None:
        if attempts_so_far >= self.max_retries or not failures:
            return None
        req = copy.deepcopy(request)
        msgs = list(req.get("messages") or [])
        assistant = {
            k: v
            for k, v in assistant_message.items()
            if k in ("role", "content", "tool_calls", "reasoning_content", "reasoning")
        }
        assistant.setdefault("role", "assistant")
        msgs.append(assistant)
        failed = {f.call_id: f for f in failures}
        for tc in assistant.get("tool_calls") or []:
            f = failed.get(tc.get("id"))
            text = (
                f"{RETRY_MARKER} JSON ({f.parse_error}). Call the tool again with a valid JSON object of arguments."
                if f
                else "Not executed: another call in the same turn had invalid arguments. Call it again."
            )
            msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "content": text})
        req["messages"] = msgs
        req["stream"] = False
        req.pop("stream_options", None)
        return RetryPlan(request=req, reason="tool-call arguments failed to parse")
