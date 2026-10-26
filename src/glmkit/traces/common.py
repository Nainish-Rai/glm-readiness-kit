"""Helpers shared by adapters: timestamps, ids, usage normalisation, message and tool conversion."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from glmkit.contracts.trace import (
    Part,
    TraceMessage,
    TraceTool,
    TraceUsage,
    _openai_message,
    _text_parts,
)
from glmkit.traces.registry import as_int

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def parse_ts(v: Any) -> datetime | None:
    """ISO 8601 string, or epoch seconds / milliseconds / microseconds / nanoseconds (int, float, numeric str)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str):
        try:
            n = float(v)
        except ValueError:
            try:
                d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return None
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        v = n
    if isinstance(v, int | float):
        for limit, div in ((1e17, 1e9), (1e14, 1e6), (1e11, 1e3)):
            if abs(v) >= limit:
                v = v / div
                break
        try:
            return datetime.fromtimestamp(v, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def ms_between(a: Any, b: Any) -> float | None:
    da, db = parse_ts(a), parse_ts(b)
    return round((db - da).total_seconds() * 1000, 3) if da and db else None


def json_or_raw(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def openai_usage(u: dict[str, Any] | None) -> TraceUsage:
    """OpenAI convention: ``prompt_tokens`` already includes cached tokens."""
    if not u:
        return TraceUsage()
    pd = u.get("prompt_tokens_details") or {}
    cd = u.get("completion_tokens_details") or {}
    return TraceUsage(
        input_tokens_total=as_int(u.get("prompt_tokens", u.get("input_tokens"))),
        cache_read_tokens=as_int(pd.get("cached_tokens")),
        cache_write_tokens=as_int(pd.get("cache_write_tokens")),
        output_tokens_total=as_int(u.get("completion_tokens", u.get("output_tokens"))),
        reasoning_tokens=as_int(cd.get("reasoning_tokens")),
        source_convention="openai",
    )


def anthropic_usage(u: dict[str, Any] | None) -> TraceUsage:
    """Anthropic convention: ``input_tokens`` EXCLUDES cache reads and writes. Convert to total-including-cache."""
    if not u:
        return TraceUsage()
    inp = as_int(u.get("input_tokens")) or 0
    read = as_int(u.get("cache_read_input_tokens")) or 0
    cc = u.get("cache_creation") or {}
    write = as_int(u.get("cache_creation_input_tokens"))
    if write is None:
        write = (as_int(cc.get("ephemeral_5m_input_tokens")) or 0) + (as_int(cc.get("ephemeral_1h_input_tokens")) or 0)
    details = u.get("output_tokens_details") or {}
    return TraceUsage(
        input_tokens_total=inp + read + write,
        cache_read_tokens=read,
        cache_write_tokens=write,
        output_tokens_total=as_int(u.get("output_tokens")),
        reasoning_tokens=as_int(details.get("thinking_tokens")),
        source_convention="anthropic",
    )


def openai_messages(messages: list[dict[str, Any]]) -> tuple[list[Part], list[TraceMessage]]:
    system: list[Part] = []
    out: list[TraceMessage] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") in ("system", "developer"):
            system += _text_parts(m.get("content"))
        else:
            out.append(_openai_message(m))
    return system, out


def anthropic_messages(system: Any, messages: list[dict[str, Any]]) -> tuple[list[Part], list[TraceMessage]]:
    msgs = [
        TraceMessage(role=str(m.get("role", "user")), parts=_text_parts(m.get("content")))
        for m in messages or []
        if isinstance(m, dict)
    ]
    return _text_parts(system), msgs


def openai_tools(tools: list[dict[str, Any]] | None) -> list[TraceTool]:
    out = []
    for t in tools or []:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        out.append(
            TraceTool(name=fn.get("name", ""), description=fn.get("description"), parameters=fn.get("parameters") or {})
        )
    return out


def anthropic_tools(tools: list[dict[str, Any]] | None) -> list[TraceTool]:
    return [
        TraceTool(name=t.get("name", ""), description=t.get("description"), parameters=t.get("input_schema") or {})
        for t in tools or []
        if isinstance(t, dict)
    ]


def text_of(parts: list[Part]) -> str:
    return "".join(p.text or "" for p in parts if p.type == "text")
