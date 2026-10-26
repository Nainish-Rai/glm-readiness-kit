"""Golden sets: the items an eval replays. Formats (docs/contracts.md, "Eval"):

* ``*.yaml`` / ``*.yml`` / ``*.json``: one document ``{schema_version, name, k, params, graders, items}``;
* ``*.jsonl``: one item per line, either a golden item or a SwitchTrace v0 call (``"schema": "switch-eval.call/0"``).
  A trace becomes an item: its recorded request is replayed, ``labels.expected`` (if any) is the reference, the
  recorded response is the fallback reference for a judge, and the cluster is the trace id.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SET_SCHEMA_VERSION = 1


class GoldenError(ValueError):
    pass


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_type: str = "default"
    cluster: str | None = Field(
        default=None, description="Items sharing a cluster (session, ticket, customer) are resampled together"
    )
    system: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list, description="OpenAI-style chat messages")
    prompt: str | None = Field(default=None, description="Shorthand for one user message")
    tools: list[dict[str, Any]] | None = None
    params: dict[str, Any] = Field(default_factory=dict, description="Request params: max_tokens, temperature, ...")
    expected: Any = Field(default=None, description="Reference answer for exact/regex/numeric/sql/judge graders")
    recorded: str | None = Field(
        default=None, description="Incumbent output recorded in a trace (judge fallback reference)"
    )
    grader: dict[str, Any] | None = Field(
        default=None, description="Grader config; falls back to the task type's default"
    )
    k: int | None = Field(default=None, ge=1, description="Replays per target for this item (stochastic items)")
    critical: bool = Field(default=False, description="List regressions on this item separately in the report")
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _messages(self) -> GoldenItem:
        if self.prompt is not None and not self.messages:
            self.messages = [{"role": "user", "content": self.prompt}]
        if not self.messages:
            raise ValueError(f"item {self.id!r} needs 'messages' or 'prompt'")
        return self

    @property
    def cluster_id(self) -> str:
        return self.cluster or self.id

    def canonical_request(self, extra_params: dict[str, Any] | None = None) -> dict[str, Any]:
        msgs = list(self.messages)
        if self.system:
            msgs = [{"role": "system", "content": self.system}, *msgs]
        req: dict[str, Any] = {"messages": msgs}
        if self.tools:
            req["tools"] = self.tools
        for k, v in {**(extra_params or {}), **self.params}.items():
            req[k] = v
        return req


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SET_SCHEMA_VERSION
    name: str = "golden-set"
    description: str = ""
    k: int = Field(default=1, ge=1, description="Default replays per target")
    params: dict[str, Any] = Field(default_factory=dict, description="Default request params for every item")
    graders: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Default grader per task type; '*' = any"
    )
    items: list[GoldenItem]

    @model_validator(mode="after")
    def _unique(self) -> GoldenSet:
        seen: set[str] = set()
        for it in self.items:
            if it.id in seen:
                raise ValueError(f"duplicate item id {it.id!r}")
            seen.add(it.id)
        return self

    def grader_for(self, item: GoldenItem) -> dict[str, Any]:
        if item.grader:
            return item.grader
        for key in (item.task_type, "*"):
            if key in self.graders:
                return self.graders[key]
        if item.expected is not None:
            return {"type": "exact"}
        if item.recorded is not None:
            return {"type": "judge"}
        raise GoldenError(f"item {item.id!r} has no grader: set one on the item or under graders[{item.task_type!r}]")

    def k_for(self, item: GoldenItem) -> int:
        return item.k or self.k

    def fingerprint(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ traces


def _parts_text(parts: list[dict[str, Any]]) -> str:
    return "".join(p.get("text") or "" for p in parts if p.get("type") == "text")


def _trace_messages(trace: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    req = trace["request"]
    sys_text = _parts_text(req.get("system") or [])
    if sys_text:
        out.append({"role": "system", "content": sys_text})
    for m in req.get("messages") or []:
        parts = m.get("parts") or []
        calls = [
            {
                "id": p.get("id"),
                "type": "function",
                "function": {"name": p.get("name"), "arguments": json.dumps(p.get("arguments") or {})},
            }
            for p in parts
            if p.get("type") == "tool_call"
        ]
        results = [p for p in parts if p.get("type") == "tool_result"]
        if results:
            for r in results:
                c = r.get("content")
                out.append(
                    {"role": "tool", "tool_call_id": r.get("id"), "content": c if isinstance(c, str) else json.dumps(c)}
                )
            continue
        msg: dict[str, Any] = {"role": m.get("role", "user"), "content": _parts_text(parts)}
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)
    return out


def trace_to_item(trace: dict[str, Any]) -> GoldenItem | None:
    """A replayable item from a SwitchTrace call, or None for rows without content (privacy.content_present false)."""
    if not (trace.get("privacy") or {}).get("content_present", True):
        return None
    req = trace["request"]
    sys_text = ""
    msgs = _trace_messages(trace)
    if msgs and msgs[0]["role"] == "system":
        sys_text = msgs.pop(0)["content"]
    params = {
        k: v
        for k, v in (req.get("params") or {}).items()
        if k in ("temperature", "top_p", "max_tokens") and v is not None
    }
    resp = trace.get("response") or {}
    recorded = "".join(_parts_text(m.get("parts") or []) for m in resp.get("messages") or []) or None
    expected = (trace.get("labels") or {}).get("expected")
    tools = [{"type": "function", "function": t} for t in req.get("tools") or []] or None
    return GoldenItem(
        id=trace["call_id"],
        task_type=trace.get("task_type") or "default",
        cluster=trace.get("trace_id"),
        system=sys_text or None,
        messages=msgs,
        tools=tools,
        params=params,
        expected=expected,
        recorded=recorded,
        meta={"source": trace.get("source"), "trace_id": trace.get("trace_id")},
    )


def load_golden_set(path: Path | str) -> GoldenSet:
    p = Path(path)
    if not p.is_file():
        raise GoldenError(f"golden set not found: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        if p.suffix == ".jsonl":
            items: list[GoldenItem] = []
            for _n, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("schema") == "switch-eval.call/0":
                    it = trace_to_item(row)
                    if it is not None:
                        items.append(it)
                else:
                    items.append(GoldenItem.model_validate(row))
            return GoldenSet(name=p.stem, items=items)
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise GoldenError(f"{p}: expected a mapping with 'items'")
        return GoldenSet.model_validate(data)
    except GoldenError:
        raise
    except Exception as e:
        raise GoldenError(f"{p}: {e}") from e
