"""One model call for the eval: send, retry throttling, reduce the Exchange to a small serialisable record.

Throttled responses (HTTP 429, Z.ai codes 1302/1305/1308-1321) are retried with backoff (``Retry-After`` wins) and
counted in ``throttled_retries``. A call that stays throttled after all retries has status ``throttled`` and is
excluded from scoring, never counted as a failure. Any other error status is a real failure of the target.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from glmkit.client import GLMClient
from glmkit.contracts.evidence import Exchange

THROTTLE_CODES = {"1302", "1305", *(str(c) for c in range(1308, 1322))}


@dataclass
class CallOutcome:
    status: str  # ok | error | throttled
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int | None] = field(default_factory=dict)  # SwitchTrace usage names
    latency_ms: float | None = None
    http_status: int | None = None
    error: str | None = None
    attempts: int = 1
    throttled_retries: int = 0

    def output_text(self) -> str:
        """What graders see: the text, or the tool calls as JSON when the model only called tools."""
        if self.content or not self.tool_calls:
            return self.content
        return json.dumps(
            [{"name": c["name"], "arguments": c.get("arguments")} for c in self.tool_calls], ensure_ascii=False
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> CallOutcome:
        return cls(**d)


def is_throttled(ex: Exchange) -> bool:
    n = ex.normalized
    return n.http_status == 429 or (n.error_code or "") in THROTTLE_CODES


def retry_after_s(ex: Exchange) -> float | None:
    h = (ex.response.headers if ex.response else {}) or {}
    for k, v in h.items():
        if k.lower() == "retry-after":
            try:
                return max(0.0, float(v))
            except ValueError:
                return None
    return None


def outcome_from_exchange(ex: Exchange) -> CallOutcome:
    n = ex.normalized
    u = n.usage
    ok = ex.transport_error is None and n.http_status is not None and 200 <= n.http_status < 300
    return CallOutcome(
        status="ok" if ok else "error",
        content=n.content or "",
        tool_calls=[
            {"name": c.name, "arguments": c.arguments_parsed if c.arguments_parsed is not None else c.arguments}
            for c in n.tool_calls
        ],
        finish_reason=n.finish_reason_norm,
        usage={
            "input_tokens_total": u.prompt_tokens if u else None,
            "cache_read_tokens": u.cached_tokens if u else None,
            "output_tokens_total": u.completion_tokens if u else None,
            "reasoning_tokens": u.reasoning_tokens if u else None,
        },
        latency_ms=ex.timing.total_ms,
        http_status=n.http_status,
        error=None if ok else (ex.transport_error or n.error_message or f"HTTP {n.http_status}"),
    )


async def call_model(
    client: GLMClient,
    canon: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    max_retries: int = 6,
    backoff_s: float = 0.5,
    max_backoff_s: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CallOutcome:
    throttled = 0
    for attempt in range(1, max_retries + 2):
        ex = await client.send_canonical(canon, stream=False, headers=headers)
        if not is_throttled(ex):
            out = outcome_from_exchange(ex)
            out.attempts, out.throttled_retries = attempt, throttled
            return out
        throttled += 1
        if attempt > max_retries:
            break
        await sleep(min(max_backoff_s, retry_after_s(ex) or backoff_s * 2 ** (attempt - 1)))
    return CallOutcome(
        status="throttled",
        error="throttled after retries",
        http_status=429,
        attempts=max_retries + 1,
        throttled_retries=throttled,
    )
