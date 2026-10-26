"""Versioned probe sets and capability canaries (``hostq/data/*.json``).

Each set has an id: the first 12 hex digits of the SHA-256 of its canonical JSON. A reference fingerprint records
the ids it was built from, so changing any probe set changes the reference id and a stale reference is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
SETS = {"action": "action-probes-v1.json", "text": "text-probes-v1.json", "canaries": "canaries-v1.json"}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: Any, n: int = 12) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()[:n]


@dataclass(frozen=True)
class ProbeSet:
    key: str
    file: str
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return digest(self.data)

    @property
    def name(self) -> str:
        return f"{self.data['name']}-v{self.data['version']}"


@cache
def _load_cached(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_set(key: str, data_dir: Path | None = None) -> ProbeSet:
    file = SETS[key]
    data = _load_cached(str((data_dir or DATA_DIR) / file))
    return ProbeSet(key=key, file=file, data=data)


def probe_set_ids(keys: list[str] | None = None, data_dir: Path | None = None) -> dict[str, str]:
    return {k: load_set(k, data_dir).id for k in (keys or list(SETS))}


# ------------------------------------------------------------------ request builders


def action_request(ps: ProbeSet, index: int, *, temperature: float | None = 1.0, max_tokens: int = 2048) -> dict:
    """Canonical request for action prompt ``index``; the tool order is rotated per prompt so a host with a
    position bias shows it in the histogram."""
    tools = ps.data["tools"]
    k = index % len(tools)
    p = ps.data["prompts"][index]
    return {
        "messages": [{"role": "user", "content": p["prompt"]}],
        "tools": tools[k:] + tools[:k],
        "tool_choice": "auto",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "params": {"reasoning_effort": "low"},
    }


def text_request(ps: ProbeSet, index: int, *, temperature: float = 1.0, max_tokens: int = 128) -> dict:
    p = ps.data["prompts"][index]
    return {
        "messages": [{"role": "user", "content": p["prompt"]}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "params": {"reasoning_effort": "low"},
    }


def filler_text(ps: ProbeSet, tokens: int) -> str:
    """Deterministic filler of about ``tokens`` tokens (4 characters each, the mock's estimate)."""
    sentences = ps.data["filler"]
    out: list[str] = []
    size, i = 0, 0
    while size < tokens * 4:
        s = f"[{i:05d}] {sentences[i % len(sentences)]}"
        out.append(s)
        size += len(s) + 1
        i += 1
    return "\n".join(out)


def needle_prompt(ps: ProbeSet, item: dict[str, Any], tokens: int | None = None) -> str:
    lines = filler_text(ps, tokens or item["tokens"]).split("\n")
    at = int(len(lines) * item["depth"])
    lines.insert(at, item["needle"])
    return "\n".join(lines)


def canary_items(ps: ProbeSet) -> list[dict[str, Any]]:
    return [*ps.data["bfcl"], *ps.data["math"], *ps.data["needle"]]


def canary_request(ps: ProbeSet, item: dict[str, Any], *, needle_tokens: int | None = None) -> dict:
    tag = f"[canary {item['id']}]"
    req: dict[str, Any] = {"temperature": 0, "max_tokens": 2048, "params": {"reasoning_effort": "low"}}
    if item["family"] == "bfcl":
        req["messages"] = [{"role": "user", "content": f"{tag} {item['prompt']}"}]
        req["tools"] = item["tools"]
        req["tool_choice"] = "auto"
    elif item["family"] == "math":
        req["messages"] = [{"role": "user", "content": f"{tag} {item['prompt']}"}]
    else:
        body = needle_prompt(ps, item, needle_tokens)
        req["messages"] = [{"role": "user", "content": f"{tag}\n{body}\n\n{item['question']}"}]
    return req


# ------------------------------------------------------------------ scoring


def _norm_val(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip().lower()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return v


def _call_matches(want: dict[str, Any], got: Any) -> bool:
    if got.name != want["name"] or not isinstance(got.arguments_parsed, dict):
        return False
    return all(_norm_val(got.arguments_parsed.get(k)) == _norm_val(v) for k, v in want["arguments"].items())


def score_canary(item: dict[str, Any], response: Any) -> bool:
    """True when the response answers the canary correctly. ``response`` is a ``NormalizedResponse``."""
    if item["family"] == "bfcl":
        calls = list(response.tool_calls or [])
        wants = list(item["expected_calls"])
        if len(calls) != len(wants):
            return False
        for w in wants:
            hit = next((c for c in calls if _call_matches(w, c)), None)
            if hit is None:
                return False
            calls.remove(hit)
        return True
    text = (response.content or "").lower()
    want = re.escape(item["expected_text"].lower())
    return re.search(rf"(?<![\w-]){want}(?![\w-])", text) is not None


# ------------------------------------------------------------------ mock support


def mock_rules(ps: ProbeSet) -> list[dict[str, Any]]:
    """Scripted mock replies that answer every canary correctly (the mock is not a model, so the canaries
    need an oracle). Pass as ``MockConfig(rules=...)``; defects such as ``quantized`` then degrade them."""
    rules = []
    for item in canary_items(ps):
        if item["family"] == "bfcl":
            reply: dict[str, Any] = {"tool_calls": item["expected_calls"]}
        else:
            reply = {"content": item["expected_text"]}
        rules.append(
            {"name": item["id"], "match": {"last_user_regex": rf"\[canary {re.escape(item['id'])}\]"}, "reply": reply}
        )
    return rules
