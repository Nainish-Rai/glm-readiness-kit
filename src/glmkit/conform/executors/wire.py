"""Executors for the wire-contract cases (slice 03).

``wire_single``       one request; optional body padding (WIRE-010, WIRE-018) and a case-level stream timeout
                      (WIRE-017). Large strings are cut in the evidence, ``body_bytes`` keeps the real size.
``effort_sweep``      the same prompt at several reasoning_effort levels, a few runs each (WIRE-003).
``paired_stream``     the same request non-stream then stream (WIRE-016).
``auth_probe``        the same request with different credentials / headers (ANTH-002, AUTH-001).
``corpora``           token accounting: one request per text corpus plus a count_tokens reference (ANTH-004).
``burst``             N concurrent requests (LIM-001).
``thinking_roundtrip`` a request, and when it ends in a tool call, a second turn that sends the thinking block
                      back (ANTH-003).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import os
from collections.abc import Iterator
from typing import Any

from glmkit.client import GLMClient, deep_merge
from glmkit.conform.executors import ExecContext, executor
from glmkit.contracts.evidence import Exchange
from glmkit.mock.engine import INVALID_KEY, STRICT_AUTH_HEADER

FILLER = "lorem ipsum dolor sit amet consectetur adipiscing elit "
EVIDENCE_STR_LIMIT = 600
MOCK_PLACEHOLDER_KEY = "glmkit-mock-key"  # sent to keyless targets so a strict-auth probe has a credential


def compact(ex: Exchange, limit: int = EVIDENCE_STR_LIMIT) -> Exchange:
    """Cut very long strings in the recorded request body (padding, corpora). ``body_bytes`` is unchanged."""

    def cut(v: Any) -> Any:
        if isinstance(v, str) and len(v) > limit:
            return f"{v[: limit // 2]} ... [{len(v)} chars; cut in the evidence] ... {v[-limit // 6 :]}"
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            return [cut(x) for x in v]
        return v

    ex.request.body = cut(ex.request.body)
    return ex


def pad_last_user(request: dict[str, Any], chars: int) -> dict[str, Any]:
    """Append ``chars`` characters of filler text to the last user message."""
    req = copy.deepcopy(request)
    filler = (FILLER * (chars // len(FILLER) + 1))[:chars]
    for m in reversed(req.get("messages") or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            m["content"] = f"{m['content']}\n{filler}"
            return req
    req.setdefault("messages", []).append({"role": "user", "content": filler})
    return req


@contextlib.contextmanager
def stream_deadline(client: GLMClient, seconds: float | None) -> Iterator[None]:
    """Lower the client's whole-stream deadline for one case (never raise it above the run-level setting)."""
    old = client.stream_timeout_s
    if seconds:
        client.stream_timeout_s = min(old, float(seconds))
    try:
        yield
    finally:
        client.stream_timeout_s = old


@executor("wire_single")
async def wire_single(ctx: ExecContext) -> list[Exchange]:
    """config: pad_chars (int), stream_timeout_s (float)."""
    request = pad_last_user(ctx.request, int(ctx.config["pad_chars"])) if ctx.config.get("pad_chars") else ctx.request
    with stream_deadline(ctx.client, ctx.config.get("stream_timeout_s")):
        ex = await ctx.client.send_canonical(request, stream=ctx.stream, label="main")
    return [compact(ex)]


@executor("effort_sweep")
async def effort_sweep(ctx: ExecContext) -> list[Exchange]:
    """config: levels (default low, high, max), runs (default 3). Labels ``effort:<level>#<run>``."""
    out = []
    for lvl in ctx.config.get("levels", ["low", "high", "max"]):
        req = deep_merge(ctx.request, {"params": {"thinking": {"type": "enabled"}, "reasoning_effort": lvl}})
        for i in range(int(ctx.config.get("runs", 3))):
            out.append(await ctx.client.send_canonical(req, stream=False, label=f"effort:{lvl}#{i}"))
    return out


@executor("paired_stream")
async def paired_stream(ctx: ExecContext) -> list[Exchange]:
    """The request non-stream (label ``nonstream``), then stream (label ``stream``, the default exchange)."""
    a = await ctx.client.send_canonical(ctx.request, stream=False, label="nonstream")
    with stream_deadline(ctx.client, ctx.config.get("stream_timeout_s")):
        b = await ctx.client.send_canonical(ctx.request, stream=True, label="stream")
    return [a, b]


# ------------------------------------------------------------------ auth probes


class ProbeClient(GLMClient):
    """A client whose credentials and headers are chosen per probe.

    ``auth``: ``native`` (the profile's own style), ``x-api-key``, ``bearer``, ``invalid`` (the sentinel key,
    native style) or ``none``. ``drop`` removes headers by name. Every probe asks a mock to enforce credentials
    (``X-GLMKit-Require-Auth``); real hosts ignore that header.
    """

    def __init__(self, *a: Any, auth: str = "native", drop: tuple[str, ...] = (), strict: bool = False, **kw: Any):
        super().__init__(*a, **kw)
        self.auth, self.drop, self.strict = auth, tuple(d.lower() for d in drop), strict
        self.key_override: str | None = None

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"content-type": "application/json", "user-agent": "glmkit"}
        if self.profile.protocol == "anthropic_messages":
            from glmkit.client.adapters import ANTHROPIC_VERSION

            h["anthropic-version"] = ANTHROPIC_VERSION
        key = INVALID_KEY if self.auth == "invalid" else (self.key_override or self._key or MOCK_PLACEHOLDER_KEY)
        style = self.profile.auth_style if self.auth in ("native", "invalid") else self.auth
        if self.auth != "none":
            if style == "x-api-key":
                h["x-api-key"] = key
            else:
                h["authorization"] = f"Bearer {key}"
        h.update(self.profile.headers)
        h.update(self.extra_headers)
        if self.strict:
            h[STRICT_AUTH_HEADER] = "1"
        h.update(extra or {})
        for name in self.drop:
            h.pop(name, None)
        return h


def probe_client(ctx: ExecContext, **kw: Any) -> ProbeClient:
    c = ctx.client
    profile = kw.pop("profile", c.profile)
    return ProbeClient(
        profile,
        timeout_s=c.timeout_s,
        stream_timeout_s=c.stream_timeout_s,
        extra_headers=c.extra_headers,
        redactor=c.redactor,
        **kw,
    )


@executor("auth_probe")
async def auth_probe(ctx: ExecContext) -> list[Exchange]:
    """config.probes: list of ``{label, auth, drop?, protocols?, key_env?, base_url?}``. Exchange label
    ``auth:<label>``, or ``cross:<label>`` when the probe sets ``key_env`` (a key of another class; skipped when
    the variable is unset)."""
    out = []
    for p in ctx.config.get("probes", []):
        if p.get("protocols") and ctx.target.protocol not in p["protocols"]:
            continue
        profile = ctx.client.profile
        cross = bool(p.get("key_env"))
        alt_key = None
        if cross:
            alt_key = os.environ.get(p["key_env"])
            if not alt_key:
                continue
            ctx.client.redactor.add(alt_key)
        if p.get("base_url"):
            profile = profile.model_copy(update={"base_url": p["base_url"]})
        async with probe_client(
            ctx, profile=profile, auth=p.get("auth", "native"), drop=tuple(p.get("drop", ())), strict=True
        ) as c:
            c.key_override = alt_key
            body = c.body_for(ctx.request, stream=False)
            out.append(await c.send(body, stream=False, label=f"{'cross' if cross else 'auth'}:{p['label']}"))
    return out


# ------------------------------------------------------------------ token accounting


def corpus_text(spec: dict[str, Any]) -> str:
    return str(spec["unit"]) * int(spec.get("repeat", 1))


@executor("corpora")
async def corpora(ctx: ExecContext) -> list[Exchange]:
    """config.corpora: list of ``{name, unit, repeat}``; one request each (``corpus:<name>``) and, on the
    Anthropic protocol, a count_tokens reference (``ref:<name>``) unless ``reference: none``."""
    out = []
    want_ref = ctx.target.protocol == "anthropic_messages" and ctx.config.get("reference", "count_tokens") != "none"
    ref_profile = ctx.client.profile.model_copy(update={"path": "/v1/messages/count_tokens"})
    for spec in ctx.config.get("corpora", []):
        req = {**ctx.request, "messages": [{"role": "user", "content": corpus_text(spec)}]}
        main = await ctx.client.send_canonical(req, stream=False, label=f"corpus:{spec['name']}")
        out.append(compact(main))
        if want_ref:
            async with probe_client(ctx, profile=ref_profile) as c:
                body = c.body_for(req, stream=False)
                ref_body = {"model": body["model"], "messages": body["messages"]}
                out.append(compact(await c.send(ref_body, stream=False, label=f"ref:{spec['name']}")))
    return out


# ------------------------------------------------------------------ bursts


@executor("burst")
async def burst(ctx: ExecContext) -> list[Exchange]:
    """config: n (default 12) concurrent requests; labels ``burst:<i>``."""
    n = int(ctx.config.get("n", 12))
    return list(
        await asyncio.gather(
            *(ctx.client.send_canonical(ctx.request, stream=ctx.stream, label=f"burst:{i}") for i in range(n))
        )
    )


# ------------------------------------------------------------------ thinking round trip


@executor("thinking_roundtrip")
async def thinking_roundtrip(ctx: ExecContext) -> list[Exchange]:
    """Turn 1 as written. If the request has tools and the answer is a tool call, turn 2 replays the assistant
    content blocks (thinking with signature + tool_use) and a tool_result. Labels ``turn1`` / ``turn2``."""
    t1 = await ctx.client.send_canonical(ctx.request, stream=False, label="turn1")
    body_json = t1.response.body_json if t1.response else None
    if not ctx.request.get("tools") or not isinstance(body_json, dict) or body_json.get("type") != "message":
        return [t1]
    blocks = body_json.get("content") or []
    uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not uses:
        return [t1]
    body = ctx.client.body_for(ctx.request, stream=False)
    body["messages"] = [
        *body["messages"],
        {"role": "assistant", "content": blocks},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": u["id"], "content": "18 C and sunny"} for u in uses],
        },
    ]
    t2 = await ctx.client.send(body, stream=False, label="turn2")
    return [t1, t2]
