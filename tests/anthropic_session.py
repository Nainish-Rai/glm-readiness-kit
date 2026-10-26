"""Claude-Code-shaped scripted session, written with the Anthropic Python SDK (dev dependency only) and httpx.

It replays the requests Claude Code makes in a normal coding turn: helper requests with thinking disabled (titles,
summaries), a streaming tool loop with adaptive thinking, a history that carries a malformed ``tool_use.input``
(zai-coding-plugins#14), a request without ``max_tokens`` (#15), the context meter (``count_tokens`` against the
usage the same prompt is billed as, #12) and a ``/compact`` over a long history. Used by the tests and by
``tools/bridge-proof/anthropic_proof.py``: run it direct and bridged, compare.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx

MODEL = "glm-5.3"
API_KEY = "sk-test-not-a-real-key"
SYSTEM = [
    {
        "type": "text",
        "text": "You are Claude Code, an interactive CLI coding assistant.",
        "cache_control": {"type": "ephemeral"},
    },
    {"type": "text", "text": "Working directory: /repo. Be concise."},
]
TOOLS: list[dict[str, Any]] = [
    {
        "name": "Bash",
        "description": "Run a shell command",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
    {
        "name": "Read",
        "description": "Read a file",
        "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
    },
]
CONTEXT_TOLERANCE = 0.05


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


@dataclass
class SessionResult:
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.steps.append(Step(name, ok, detail))

    def show(self) -> str:
        return "\n".join(f"    {'PASS' if s.ok else 'FAIL'} {s.name}: {s.detail}" for s in self.steps)


def _err(e: Exception) -> str:
    if isinstance(e, anthropic.APIStatusError):
        body = e.body if isinstance(e.body, dict) else {}
        inner = body.get("error", body) if isinstance(body, dict) else {}
        return f"HTTP {e.status_code} {inner.get('message', '')}".strip()
    return f"{type(e).__name__}: {e}"[:160]


def _blocks(content: list[Any]) -> list[dict[str, Any]]:
    return [b.model_dump(exclude_none=True) for b in content]


def long_history(turns: int = 14) -> list[dict[str, Any]]:
    """A long conversation with tool use in it: what `/compact` summarises."""
    msgs: list[dict[str, Any]] = []
    for i in range(turns):
        msgs.append(
            {"role": "user", "content": f"Step {i}: please look at module_{i}.py and tell me what changed. " * 6}
        )
        msgs.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"Reading module_{i}.py. " * 5},
                    {
                        "type": "tool_use",
                        "id": f"toolu_h{i}",
                        "name": "Read",
                        "input": {"file_path": f"/repo/module_{i}.py"},
                    },
                ],
            }
        )
        msgs.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"toolu_h{i}",
                        "content": f"def f_{i}(x):\n    return x + {i}\n" * 12,
                    }
                ],
            }
        )
        msgs.append(
            {"role": "assistant", "content": f"module_{i}.py defines f_{i}, which adds {i} to its argument. " * 4}
        )
    return msgs


def run_session(base_url: str, headers: dict[str, str] | None = None) -> SessionResult:
    """``base_url`` is the Anthropic base (no ``/v1``). ``headers`` ride on every request (the mock reads
    ``X-GLMKit-Defects`` to plant defects for that request)."""
    res = SessionResult()
    client = anthropic.Anthropic(base_url=base_url, api_key=API_KEY, default_headers=headers or {}, max_retries=0)
    raw = httpx.Client(
        base_url=base_url, headers={**(headers or {}), "x-api-key": API_KEY, "anthropic-version": "2023-06-01"}
    )

    # 1. helper requests: title generation and a one-line summary, thinking disabled (Claude Code's small-model calls)
    for name in ("helper-title", "helper-summary"):
        try:
            m = client.messages.create(
                model=MODEL,
                max_tokens=64,
                system=SYSTEM,
                thinking={"type": "disabled"},
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": f"Reply with a short {name} for: fix the login bug"}],
                    }
                ],
            )
            text = "".join(b.text for b in m.content if b.type == "text")
            res.add(name, bool(text), f"stop={m.stop_reason} text={text[:40]!r}")
        except Exception as e:
            res.add(name, False, _err(e))

    # 2. main turn: streaming tool loop with adaptive thinking; message_start must carry real input usage
    try:
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "Use Bash to run command=ls in the repo"}]
        start_usage: list[int] = []
        tools_run: list[str] = []
        final_text = ""
        for _ in range(4):
            with client.messages.stream(
                model=MODEL, max_tokens=2048, system=SYSTEM, tools=TOOLS, messages=msgs, thinking={"type": "adaptive"}
            ) as s:
                for ev in s:
                    if ev.type == "message_start":
                        start_usage.append(
                            ev.message.usage.input_tokens + (ev.message.usage.cache_read_input_tokens or 0)
                        )
                m = s.get_final_message()
            content = _blocks(m.content)
            msgs.append({"role": "assistant", "content": content})
            calls = [b for b in content if b["type"] == "tool_use"]
            if not calls:
                final_text = "".join(b.get("text", "") for b in content if b["type"] == "text")
                break
            results = []
            for b in calls:
                if not isinstance(b["input"], dict) or "command" not in b["input"]:
                    raise ValueError(f"bad tool input {b['input']!r}")
                tools_run.append(b["name"])
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": "README.md\nsrc"})
            msgs.append({"role": "user", "content": results})
        res.add(
            "tool-loop-stream", bool(tools_run) and bool(final_text), f"tools={tools_run} final={final_text[:40]!r}"
        )
        res.add(
            "message-start-usage",
            bool(start_usage) and all(u > 0 for u in start_usage),
            f"message_start input tokens per stream: {start_usage}",
        )
    except Exception as e:
        res.add("tool-loop-stream", False, _err(e))

    # 3. a history entry with tool_use.input as an array (zai-coding-plugins#14)
    bad_history = [
        {"role": "user", "content": "Use Read for file_path=/repo/a.py"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_bad", "name": "Read", "input": [{"file_path": "/repo/a.py"}]}
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_bad", "content": "x = 1"}]},
    ]
    try:
        m = client.messages.create(
            model=MODEL, max_tokens=256, tools=TOOLS, messages=bad_history, thinking={"type": "adaptive"}
        )
        res.add("malformed-history", True, f"stop={m.stop_reason}")
    except Exception as e:
        res.add("malformed-history", False, _err(e))

    # 4. no max_tokens (zai-coding-plugins#15)
    r = raw.post("/v1/messages", json={"model": MODEL, "messages": [{"role": "user", "content": "Say OK"}]})
    res.add("missing-max-tokens", r.status_code == 200, f"HTTP {r.status_code}")

    # 5. /compact over a long history, thinking disabled, big max_tokens, streamed
    hist = long_history()
    compact = [
        *hist,
        {"role": "user", "content": "Your task is to create a detailed summary of the conversation so far."},
    ]
    try:
        with client.messages.stream(
            model=MODEL, max_tokens=20000, system=SYSTEM, messages=compact, thinking={"type": "disabled"}
        ) as s:
            for _ in s:
                pass
            m = s.get_final_message()
        text = "".join(b.text for b in m.content if b.type == "text")
        res.add("compact", bool(text), f"{len(compact)} messages, summary={text[:40]!r}")
    except Exception as e:
        res.add("compact", False, _err(e))

    # 6. context meter: count_tokens vs what the same prompt is billed as
    try:
        probe = [*hist, {"role": "user", "content": "meter probe"}]
        counted = client.messages.count_tokens(model=MODEL, system=SYSTEM, tools=TOOLS, messages=probe).input_tokens
        u = client.messages.create(model=MODEL, max_tokens=16, system=SYSTEM, tools=TOOLS, messages=probe).usage
        billed = u.input_tokens + (u.cache_read_input_tokens or 0)
        off = abs(counted - billed) / max(billed, 1)
        res.add("context-meter", off <= CONTEXT_TOLERANCE, f"count_tokens={counted} billed={billed} off={off:.1%}")
    except Exception as e:
        res.add("context-meter", False, _err(e))
    raw.close()
    return res


def tool_loop(base_url: str, *, stream: bool, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """The acceptance-criteria loop: Anthropic SDK, tools, thinking, optional streaming. Returns what happened."""
    client = anthropic.Anthropic(base_url=base_url, api_key=API_KEY, default_headers=headers or {}, max_retries=0)
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "Use get_weather for city=Paris and then tell me"}]
    tool = {
        "name": "get_weather",
        "description": "Weather for a city",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    }
    results: list[str] = []
    events: list[str] = []
    try:
        for _ in range(4):
            if stream:
                with client.messages.stream(
                    model=MODEL, max_tokens=512, tools=[tool], messages=msgs, thinking={"type": "adaptive"}
                ) as s:
                    for ev in s:
                        events.append(ev.type)
                    m = s.get_final_message()
            else:
                m = client.messages.create(
                    model=MODEL, max_tokens=512, tools=[tool], messages=msgs, thinking={"type": "adaptive"}
                )
            content = _blocks(m.content)
            msgs.append({"role": "assistant", "content": content})
            calls = [b for b in content if b["type"] == "tool_use"]
            if not calls:
                return {
                    "ok": True,
                    "results": results,
                    "events": events,
                    "text": "".join(b.get("text", "") for b in content if b["type"] == "text"),
                    "thinking": any(b["type"] == "thinking" and b.get("signature") for b in content),
                }
            out = []
            for b in calls:
                if not isinstance(b["input"], dict) or "city" not in b["input"]:
                    raise ValueError(f"bad tool input {json.dumps(b['input'])}")
                results.append(f"sunny in {b['input']['city']}")
                out.append({"type": "tool_result", "tool_use_id": b["id"], "content": results[-1]})
            msgs.append({"role": "user", "content": out})
        return {"ok": False, "error": "loop did not finish", "results": results, "events": events}
    except Exception as e:
        return {"ok": False, "error": _err(e), "results": results, "events": events}
