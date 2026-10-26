"""Evidence records: one ``Exchange`` per HTTP call, with the raw request/response (secrets redacted), the
parsed SSE events, a protocol-neutral ``NormalizedResponse`` and timings. Every component that calls a model
attaches exchanges to its result items so the dashboard drawer can show exactly what happened."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_BODY_CHARS = 65_536
MAX_EVENTS = 2_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpRequestRecord(_Strict):
    method: str = "POST"
    url: str
    headers: dict[str, str] = Field(default_factory=dict, description="Sensitive headers are '[REDACTED]'")
    body: Any = None
    body_bytes: int = 0


class SSEEventRecord(_Strict):
    event: str | None = None
    data: Any = Field(description="Parsed JSON when possible, else the raw data string ('[DONE]' stays a string)")
    t_ms: float | None = Field(default=None, description="Milliseconds since the request was sent")


class HttpResponseRecord(_Strict):
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None
    body_text: str | None = Field(default=None, description=f"Raw body, truncated to {MAX_BODY_CHARS} chars")
    body_json: Any = None
    truncated: bool = False
    events: list[SSEEventRecord] | None = Field(default=None, description="SSE events for streamed responses")
    events_truncated: bool = False


class Timing(_Strict):
    ttfb_ms: float | None = Field(default=None, description="Time to first response byte")
    ttft_ms: float | None = Field(
        default=None, description="Time to first token: first event carrying reasoning, text or a tool call"
    )
    total_ms: float | None = None


class NormToolCall(_Strict):
    index: int = 0
    id: str | None = None
    name: str | None = None
    arguments: str | None = Field(default=None, description="Arguments exactly as received (JSON string)")
    arguments_parsed: Any = None
    parse_error: str | None = None


class NormUsage(_Strict):
    """Usage in the OpenAI/OTel convention: prompt_tokens includes cached tokens."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    source_convention: Literal["openai", "anthropic"] | None = None
    raw: dict[str, Any] | None = None


class StreamMeta(_Strict):
    event_count: int = 0
    done_seen: bool = Field(default=False, description="OpenAI '[DONE]' or Anthropic 'message_stop' seen")
    finish_events: int = Field(default=0, description="Events that carried a finish/stop reason")
    role_events: int = 0
    first_usage: dict[str, Any] | None = Field(
        default=None, description="Usage carried by the first event (Anthropic message_start)"
    )
    timed_out: bool = False
    parse_errors: int = 0


class NormalizedResponse(_Strict):
    """Protocol-neutral view of a response; checks read this."""

    http_status: int | None = None
    ok: bool = False
    error_code: str | None = Field(default=None, description="Z.ai business code as a string ('1210')")
    error_code_type: Literal["string", "integer"] | None = None
    error_type: str | None = None
    error_message: str | None = None
    model: str | None = None
    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[NormToolCall] = Field(default_factory=list)
    finish_reason: str | None = Field(default=None, description="Raw finish_reason / stop_reason")
    finish_reason_norm: str | None = Field(default=None, description="stop | tool_calls | length | other value")
    usage: NormUsage | None = None
    stream: StreamMeta | None = None


class Exchange(_Strict):
    id: str
    label: str = "main"
    protocol: str
    mode: Literal["nonstream", "stream"]
    request: HttpRequestRecord
    response: HttpResponseRecord | None = None
    normalized: NormalizedResponse = Field(default_factory=NormalizedResponse)
    timing: Timing = Field(default_factory=Timing)
    transport_error: str | None = Field(default=None, description="connect error, timeout, stream_timeout ...")
