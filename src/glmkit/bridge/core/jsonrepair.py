"""Tolerant repair of tool-call argument strings. Pure, no dependencies.

``repair_arguments(raw)`` returns ``(obj, fixes)`` where ``obj`` is a dict, or raises ``ArgumentsUnrepairable``
with the original parse error. It never invents data: it only removes syntax noise (code fences, comments,
trailing commas), converts quote style and Python literals, unwraps a JSON string that itself holds JSON, unwraps a
one-element array, and closes brackets/strings that a truncated stream left open.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ArgumentsUnrepairable(ValueError):
    def __init__(self, message: str, parse_error: str) -> None:
        super().__init__(message)
        self.parse_error = parse_error


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
_PY_LITERALS = {"True": "true", "False": "false", "None": "null"}


def parse_error_of(raw: str) -> str:
    try:
        json.loads(raw)
    except ValueError as e:
        return f"{type(e).__name__}: {e}"
    return ""


def _tokenize_fix(s: str) -> tuple[str, list[str]]:
    """One pass over ``s``: single-quoted strings -> double-quoted, comments dropped, Python literals -> JSON,
    trailing commas dropped, unquoted keys quoted, and unclosed strings/brackets closed."""
    out: list[str] = []
    fixes: set[str] = set()
    stack: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            quote, j, buf = c, i + 1, []
            while j < n:
                ch = s[j]
                if ch == "\\" and j + 1 < n:
                    nxt = s[j + 1]
                    buf.append("'" if nxt == "'" else ch + nxt)
                    j += 2
                    continue
                if ch == quote:
                    break
                if ch == '"':  # a raw double quote inside a single-quoted string
                    buf.append('\\"')
                elif ch == "\n":
                    buf.append("\\n")
                else:
                    buf.append(ch)
                j += 1
            else:
                fixes.add("closed-string")
            if quote == "'":
                fixes.add("single-quotes")
            out.append('"' + "".join(buf) + '"')
            i = j + 1
        elif c == "/" and s[i : i + 2] in ("//", "/*"):
            end = s.find("\n", i) if s[i + 1] == "/" else s.find("*/", i + 2)
            fixes.add("comment")
            i = n if end < 0 else end + (1 if s[i + 1] == "/" else 2)
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
            out.append(c)
            i += 1
        elif c in "}]":
            if stack and stack[-1] == c:
                stack.pop()
            else:
                fixes.add("unbalanced")
                i += 1
                continue
            # trailing comma before the closer
            k = len(out) - 1
            while k >= 0 and not out[k].strip():
                k -= 1
            if k >= 0 and out[k] == ",":
                out[k] = ""
                fixes.add("trailing-comma")
            out.append(c)
            i += 1
        elif c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_$.-]*", s[i:])
            assert m
            word = m.group(0)
            rest = s[i + len(word) :].lstrip()
            if word in ("true", "false", "null"):
                out.append(word)
            elif word in _PY_LITERALS:
                out.append(_PY_LITERALS[word])
                fixes.add("python-literal")
            elif rest.startswith(":"):
                out.append(f'"{word}"')
                fixes.add("unquoted-key")
            else:
                out.append(word)
            i += len(word)
        else:
            out.append(c)
            i += 1
    # close what a truncated stream left open
    tail = "".join(out).rstrip()
    if stack:
        fixes.add("closed-brackets")
        tail = re.sub(r",\s*$", "", tail)
        if tail.endswith(":"):  # a key with no value: drop the dangling key
            tail = re.sub(r',?\s*"(?:[^"\\]|\\.)*"\s*:$', "", tail)
        tail += "".join(reversed(stack))
    return tail, sorted(fixes)


def repair_arguments(raw: Any) -> tuple[dict[str, Any], list[str]]:
    fixes: list[str] = []
    if isinstance(raw, dict):
        return raw, fixes
    if raw is None:
        raise ArgumentsUnrepairable("arguments are null", "arguments missing")
    if not isinstance(raw, str):
        raw = json.dumps(raw)
        fixes.append("not-a-string")
    text = raw.strip()
    err = parse_error_of(text) if text else "empty arguments"
    for _ in range(3):  # a string that holds JSON that holds a string ...
        try:
            val = json.loads(text)
        except ValueError:
            m = _FENCE.match(text)
            if m:
                text = m.group(1)
                fixes.append("code-fence")
                continue
            if not text:
                break
            fixed, f = _tokenize_fix(text)
            if not f:
                break
            try:
                val = json.loads(fixed)
            except ValueError:
                break
            fixes += f
            text = json.dumps(val)
            continue
        if isinstance(val, str):
            fixes.append("double-encoded")
            text = val.strip()
            continue
        if isinstance(val, list) and len(val) == 1 and isinstance(val[0], dict):
            fixes.append("unwrapped-array")
            return val[0], fixes
        if isinstance(val, dict):
            return val, fixes
        raise ArgumentsUnrepairable(f"arguments are a JSON {type(val).__name__}, not an object", err or "not an object")
    raise ArgumentsUnrepairable("arguments are not valid JSON", err or parse_error_of(text))
