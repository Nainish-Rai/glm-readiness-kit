"""Data types shared by the mock engine, encoders and defect modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockConfig:
    """Server-level behaviour. Everything is deterministic for a given config and request sequence."""

    endpoint_class: str = "api"  # class for paths without '/coding/' (those are always 'plan')
    defects: list[str] = field(default_factory=list)  # server-wide named defects
    rules: list[dict[str, Any]] = field(default_factory=list)  # scripted replies (see engine.match_rule)
    chunk_chars: int = 12  # characters per streamed text delta
    chunk_delay_s: float = 0.0  # sleep between streamed events
    first_token_delay_s: float = 0.0  # sleep before the first streamed event / non-stream body
    require_key: str | None = None  # when set, requests must carry this key (401 1000/1001 otherwise)
    created: int = 1792972800  # fixed 'created' timestamp (2026-10-26T00:00:00Z) for byte-stable output


@dataclass
class Msg:
    role: str
    text: str = ""
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # {id, name, arguments(any)}
    tool_results: list[dict[str, Any]] = field(default_factory=list)  # {id, content}


@dataclass
class Tool:
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonRequest:
    """Protocol-neutral view of an incoming request."""

    model: str | None
    messages: list[Msg]
    system: str = ""
    tools: list[Tool] = field(default_factory=list)
    tool_choice: Any = "auto"  # 'auto' | 'none' | 'required' | {'name': ...}
    thinking_type: str | None = None  # enabled | disabled | adaptive | None (not sent)
    effort: str | None = None  # raw reasoning_effort / output_config.effort
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] = field(default_factory=list)
    stream: bool = False
    tool_stream: bool = False


@dataclass
class ErrorReply:
    status: int
    code: str | None
    message: str
    anthropic_type: str = "invalid_request_error"
    reason: str = ""  # machine-readable cause, e.g. 'forced_thinking', 'unknown_model'


@dataclass
class ToolCallOut:
    id: str
    name: str
    arguments: str  # JSON string (OpenAI form); Anthropic encoders parse it back to an object


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class Reply:
    id: str
    model: str
    reasoning: str | None
    content: str | None
    tool_calls: list[ToolCallOut]
    finish_reason: str  # OpenAI vocabulary: stop | tool_calls | length
    usage: Usage
    signature: str = ""


@dataclass
class MockEvent:
    """One SSE event. ``event`` is the Anthropic event name (None for OpenAI). ``data`` is a dict or '[DONE]'."""

    data: Any
    event: str | None = None
    delay_s: float = 0.0


@dataclass
class ServerState:
    requests: int = 0
    prefix_seen: set[str] = field(default_factory=set)
    defect_state: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class MockContext:
    protocol: str  # openai_chat | anthropic_messages
    path: str
    endpoint_class: str
    headers: dict[str, str]  # lower-cased names
    body: Any  # parsed JSON body (None if unparsable)
    raw: bytes
    config: MockConfig
    state: ServerState
    defects: list[Any] = field(default_factory=list)  # active Defect instances
    req: CanonRequest | None = None
    rule: dict[str, Any] | None = None
    thinking: bool = False  # resolved: will the reply carry reasoning?
    effort: str | None = None  # resolved effort level
    notes: list[str] = field(default_factory=list)  # engine decisions, e.g. 'thinking disabled -> low (plan)'
