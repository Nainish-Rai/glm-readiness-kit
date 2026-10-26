"""Offline stream reassembly: serialise a fixture's SSE events, cut the bytes at arbitrary points, and feed
them through the real parser and accumulator. Used by STRM-004 (sglang#35564) to prove the reassembly does
not depend on how the network chunked the stream or how fragments of different tool calls interleave."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from glmkit.client.adapters import StreamAccumulator, normalize
from glmkit.client.sse import SSEParser
from glmkit.contracts.evidence import Exchange, HttpRequestRecord, HttpResponseRecord, SSEEventRecord


def to_sse(events: list[dict[str, Any] | str], protocol: str = "openai_chat") -> str:
    out = []
    for e in events:
        data = e if isinstance(e, str) else json.dumps(e, ensure_ascii=False)
        name = e.get("type") if protocol == "anthropic_messages" and isinstance(e, dict) else None
        out.append((f"event: {name}\n" if name else "") + f"data: {data}\n\n")
    return "".join(out)


def split_text(text: str, how: str) -> list[str]:
    """Cut ``text`` into chunks. ``how``: ``whole``, ``char`` (1 character), ``token`` (a word or a single
    punctuation character), ``bytes<N>`` (N characters), or ``pairs`` (every 2nd and 3rd character alternating)."""
    if how == "whole":
        return [text]
    if how == "char":
        return list(text)
    if how == "token":
        return re.findall(r"\w+|\s+|[^\w\s]", text)
    if how.startswith("bytes"):
        n = int(how[5:])
        return [text[i : i + n] for i in range(0, len(text), n)]
    if how == "pairs":
        out, i, k = [], 0, 2
        while i < len(text):
            out.append(text[i : i + k])
            i += k
            k = 5 - k
        return out
    raise ValueError(f"unknown split {how!r}")


def reassemble(protocol: str, sse_text: str, how: str, label: str) -> Exchange:
    acc = StreamAccumulator(protocol, 200)
    parser = SSEParser()
    records: list[SSEEventRecord] = []

    def take(ev: Any) -> None:
        parsed: Any = ev.data
        if ev.data != "[DONE]":
            try:
                parsed = json.loads(ev.data)
            except ValueError:
                parsed = ev.data
        acc.add(ev.event, parsed)
        records.append(SSEEventRecord(event=ev.event, data=parsed))

    for piece in split_text(sse_text, how):
        for ev in parser.feed(piece):
            take(ev)
    for ev in parser.close():
        take(ev)
    return Exchange(
        id=uuid.uuid4().hex[:12],
        label=label,
        protocol=protocol,
        mode="stream",
        request=HttpRequestRecord(url="fixture://stream"),
        response=HttpResponseRecord(status=200, content_type="text/event-stream", events=records),
        normalized=acc.finish(),
    )


def reference(protocol: str, body: dict[str, Any]) -> Exchange:
    return Exchange(
        id=uuid.uuid4().hex[:12],
        label="nonstream",
        protocol=protocol,
        mode="nonstream",
        request=HttpRequestRecord(url="fixture://nonstream"),
        response=HttpResponseRecord(status=200, content_type="application/json", body_json=body),
        normalized=normalize(protocol, 200, body),
    )


def interleavings(events: list[Any], key: Any) -> list[list[Any]]:
    """Alternative event orders that keep the order within each group (``key(event)``): groups reversed, and
    round-robin. A correct per-index reassembly gives the same result for each."""
    groups: dict[Any, list[Any]] = {}
    for e in events:
        groups.setdefault(key(e), []).append(e)
    ordered = list(groups.values())
    reverse = [e for g in reversed(ordered) for e in g]
    robin: list[Any] = []
    for i in range(max(len(g) for g in ordered)):
        robin += [g[i] for g in ordered if i < len(g)]
    return [reverse, robin]
