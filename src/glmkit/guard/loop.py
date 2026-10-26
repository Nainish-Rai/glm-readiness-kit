"""Loop detector: the same tool with near-identical arguments failing again and again, or the same error text.

It is stateless: it reads the conversation the client sends with every request (OpenAI ``tool_calls`` / ``tool``
messages and Anthropic ``tool_use`` / ``tool_result`` blocks), pairs each call with its result and measures the
trailing streak. No message text is stored; the verdict carries tool names and counts only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

FAIL_RE = re.compile(
    r"(?i)\b(error|errors|failed|failure|fatal|traceback|exception|not found|no such file|permission denied|"
    r"command not found|cannot|can't|unable to|denied|exit (?:code|status)[: ]+[1-9]|non-zero exit|returned non-zero|"
    r"timed out|timeout)\b"
)
_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_HEX = re.compile(r"\b[0-9a-f]{7,}\b")
_PATHTMP = re.compile(r"/(?:tmp|var/folders)/\S+")
_WS = re.compile(r"\s+")


@dataclass
class Call:
    id: str | None
    name: str
    args: Any
    result: str | None = None
    is_error: bool = False

    @property
    def failed(self) -> bool:
        if self.result is None:
            return False
        return self.is_error or bool(FAIL_RE.search(self.result))


@dataclass
class LoopVerdict:
    streak: int = 0
    kind: str | None = None  # "same-call" | "same-error"
    tool: str | None = None

    def hit(self, threshold: int) -> bool:
        return self.streak >= threshold


def norm(text: str) -> str:
    t = _PATHTMP.sub("/TMP", text.lower())
    t = _HEX.sub("#", t)
    t = _NUM.sub("#", t)
    return _WS.sub(" ", t).strip()


def _text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(_text(x.get("text") if isinstance(x, dict) else x) for x in v)
    return "" if v is None else json.dumps(v, sort_keys=True, ensure_ascii=False)


def _args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def extract_calls(messages: list[Any]) -> list[Call]:
    """Chronological tool calls with their results (None when the call has no result yet)."""
    calls: list[Call] = []
    by_id: dict[str, Call] = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        for tc in m.get("tool_calls") or []:  # OpenAI
            fn = tc.get("function") or {}
            c = Call(tc.get("id"), fn.get("name", ""), _args(fn.get("arguments")))
            calls.append(c)
            if c.id:
                by_id[c.id] = c
        if role == "tool":  # OpenAI result
            c = by_id.get(m.get("tool_call_id") or "")
            if c is not None:
                c.result = _text(content)
        if isinstance(content, list):  # Anthropic blocks
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    c = Call(b.get("id"), b.get("name", ""), b.get("input"))
                    calls.append(c)
                    if c.id:
                        by_id[c.id] = c
                elif b.get("type") == "tool_result":
                    c = by_id.get(b.get("tool_use_id") or "")
                    if c is not None:
                        c.result = _text(b.get("content"))
                        c.is_error = bool(b.get("is_error"))
    return calls


def _same(a: Call, b: Call, similarity: float) -> bool:
    if a.name != b.name:
        return False
    x, y = (
        norm(json.dumps(a.args, sort_keys=True, ensure_ascii=False)),
        norm(json.dumps(b.args, sort_keys=True, ensure_ascii=False)),
    )
    return x == y or SequenceMatcher(None, x, y).ratio() >= similarity


def detect(messages: list[Any], *, similarity: float = 0.9) -> LoopVerdict:
    """Trailing streak of failing repeats: the larger of 'same tool + near-identical args, all failing' and
    'the same normalised error output'."""
    done = [c for c in extract_calls(messages) if c.result is not None]
    if not done:
        return LoopVerdict()
    last = done[-1]
    best = LoopVerdict()
    if last.failed:
        n = 0
        for c in reversed(done):
            if c.failed and _same(c, last, similarity):
                n += 1
            else:
                break
        best = LoopVerdict(n, "same-call", last.name)
        n = 0
        key = norm((last.result or "")[:2000])
        for c in reversed(done):
            if c.failed and norm((c.result or "")[:2000]) == key:
                n += 1
            else:
                break
        if n > best.streak:
            best = LoopVerdict(n, "same-error", last.name)
    return best


INJECTION = (
    "[glmkit-guard] You have made the same failing tool call {n} times in a row ({tool}). "
    "Stop repeating it. State what is failing, change your approach or ask the user for help."
)


def inject_message(body: dict[str, Any], verdict: LoopVerdict) -> dict[str, Any]:
    """Copy of ``body`` with a stop/rethink message appended as the newest user turn."""
    text = INJECTION.format(n=verdict.streak, tool=verdict.tool or "tool")
    out = dict(body)
    msgs = list(out.get("messages") or [])
    last = msgs[-1] if msgs else None
    if isinstance(last, dict) and last.get("role") == "user" and isinstance(last.get("content"), list):
        msgs[-1] = {**last, "content": [*last["content"], {"type": "text", "text": text}]}
    else:
        msgs.append({"role": "user", "content": text})
    out["messages"] = msgs
    return out
