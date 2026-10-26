"""Multi-turn executors for issue 04: agent loops with reasoning replay, replay branches, stream/non-stream
pairs, offline stream-split fixtures, repeated prefixes, identical repeats, parameter sweeps and the
budget-capped soak loop.

All of them return the exchanges they made; checks read them by label (``turn0``, ``seed0``, ``nonstream`` ...).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from glmkit.agentloop import streamfix, synth
from glmkit.client import deep_merge
from glmkit.conform.executors import ExecContext, executor
from glmkit.contracts.evidence import Exchange, NormalizedResponse

SOAK_BUDGET_ENV = "GLMKIT_SOAK_BUDGET_TOKENS"
DEFAULT_FOLLOWUPS = ["Continue with the next step.", "Now check one more thing in the workspace.", "Keep going."]
DEFAULT_TOOL_OUTPUT = "step {turn}: the command finished with exit code 0"


# ------------------------------------------------------------------ message building


def mutate_reasoning(text: str, how: str) -> str | None:
    """Edit replayed reasoning: ``none`` (exact), ``truncate`` (first 40%), ``edit`` (words changed),
    ``reorder`` (sentences reversed), ``drop`` (omit)."""
    if how in (None, "", "none", "exact"):
        return text
    if how == "drop":
        return None
    if how == "truncate":
        return text[: max(1, int(len(text) * 0.4))]
    if how == "edit":
        return text.replace("the ", "a ").replace("The ", "A ") + " (edited)"
    if how == "reorder":
        parts = [p for p in text.replace("\n", " ").split(". ") if p]
        return ". ".join(reversed(parts)) if len(parts) > 1 else text[::-1]
    raise ValueError(f"unknown reasoning mutation {how!r}")


def step_messages(
    n: NormalizedResponse, turn: int, cfg: dict[str, Any], *, field: str | None, mutation: str = "none"
) -> list[dict[str, Any]]:
    """The assistant message for response ``n`` (reasoning replayed in ``field``: reasoning_content | reasoning |
    both | None) followed by its tool results, or by the next user message when it called no tool."""
    msg: dict[str, Any] = {"role": "assistant", "content": n.content or None}
    calls = []
    for i, tc in enumerate(n.tool_calls):
        calls.append(
            {
                "id": tc.id or f"call_t{turn}_{i}",
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments or "{}"},
            }
        )
    if calls:
        msg["tool_calls"] = calls
    if n.content is None and not calls:
        msg["content"] = ""
    reasoning = mutate_reasoning(n.reasoning, mutation) if n.reasoning and field else None
    if reasoning:
        if field in ("reasoning_content", "both"):
            msg["reasoning_content"] = reasoning
        if field in ("reasoning", "both"):
            msg["reasoning"] = reasoning
    out = [msg]
    template = cfg.get("tool_output", DEFAULT_TOOL_OUTPUT)
    for i, c in enumerate(calls):
        out.append({"role": "tool", "tool_call_id": c["id"], "content": template.format(turn=turn, call=i)})
    if not calls:
        followups = cfg.get("followups") or DEFAULT_FOLLOWUPS
        out.append({"role": "user", "content": followups[turn % len(followups)]})
    return out


def _base_messages(ctx: ExecContext) -> list[dict[str, Any]]:
    """The case messages, with a ``prefix_tokens`` system prefix in front when the case asks for one (caching)."""
    msgs = list(ctx.request.get("messages") or [])
    if ctx.config.get("prefix_tokens"):
        msgs = [{"role": "system", "content": synth.prefix_text(int(ctx.config["prefix_tokens"]))}, *msgs]
    return msgs


def _field(cfg: dict[str, Any]) -> str | None:
    f = cfg.get("replay", "reasoning_content")
    return None if f in (None, "none", False) else f


# ------------------------------------------------------------------ agent loop


@executor("agent_loop")
async def agent_loop(ctx: ExecContext) -> list[Exchange]:
    """``turns`` sequential requests on one growing conversation. Each assistant message is replayed with its
    reasoning in ``replay`` (default ``reasoning_content``; ``none`` omits it); tool calls get a canned result and
    plain answers get the next ``followups`` user message. Stops at the first non-2xx response."""
    cfg = ctx.config
    msgs = _base_messages(ctx)
    out: list[Exchange] = []
    for i in range(int(cfg.get("turns", 3))):
        ex = await ctx.client.send_canonical({**ctx.request, "messages": msgs}, stream=ctx.stream, label=f"turn{i}")
        out.append(ex)
        if not ex.normalized.ok:
            break
        msgs = msgs + step_messages(ex.normalized, i, cfg, field=_field(cfg))
    return out


@executor("soak_loop")
async def soak_loop(ctx: ExecContext) -> list[Exchange]:
    """A long agent loop that stops at ``max_turns`` or when the decoded tokens reach the budget. The budget is
    ``$GLMKIT_SOAK_BUDGET_TOKENS`` (set by ``glmkit conform soak --budget``) or ``budget_tokens`` from the case.
    ``prompt_budget_tokens`` caps the sum of prompt tokens as well, because history grows every turn."""
    cfg = ctx.config
    budget = int(os.environ.get(SOAK_BUDGET_ENV) or cfg.get("budget_tokens", 200_000))
    prompt_cap = int(cfg.get("prompt_budget_tokens", 5_000_000))
    msgs = _base_messages(ctx)
    out: list[Exchange] = []
    decoded = prompted = 0
    turns = int(os.environ.get("GLMKIT_SOAK_MAX_TURNS") or cfg.get("max_turns", 100))
    for i in range(turns):
        ex = await ctx.client.send_canonical({**ctx.request, "messages": msgs}, stream=ctx.stream, label=f"turn{i}")
        out.append(ex)
        if not ex.normalized.ok:
            break
        u = ex.normalized.usage
        decoded += (u.completion_tokens or 0) if u else 0
        prompted += (u.prompt_tokens or 0) if u else 0
        if decoded >= budget or prompted >= prompt_cap:
            break
        msgs = msgs + step_messages(ex.normalized, i, cfg, field=_field(cfg))
    return out


@executor("replay_compare")
async def replay_compare(ctx: ExecContext) -> list[Exchange]:
    """Run ``seed_turns`` exact-replay turns, then send the next request once per branch. A branch is
    ``{label, replay: reasoning_content|reasoning|both|none, mutation: none|truncate|edit|reorder|drop,
    params: {...merged into the request}}`` and replays the last seed answer's reasoning that way."""
    cfg = ctx.config
    msgs = list(ctx.request.get("messages") or [])
    out: list[Exchange] = []
    last: NormalizedResponse | None = None
    turn = 0
    for turn in range(int(cfg.get("seed_turns", 1))):
        ex = await ctx.client.send_canonical({**ctx.request, "messages": msgs}, stream=ctx.stream, label=f"seed{turn}")
        out.append(ex)
        if not ex.normalized.ok:
            return out
        last = ex.normalized
        if turn < int(cfg.get("seed_turns", 1)) - 1:
            msgs = msgs + step_messages(last, turn, cfg, field=_field(cfg))
    assert last is not None
    for b in cfg.get("branches") or []:
        field = None if b.get("replay", "reasoning_content") in ("none", None) else b.get("replay", "reasoning_content")
        history = msgs + step_messages(last, turn, cfg, field=field, mutation=b.get("mutation", "none"))
        req = deep_merge({**ctx.request, "messages": history}, {"params": b.get("params") or {}})
        out.append(await ctx.client.send_canonical(req, stream=ctx.stream, label=b["label"]))
    return out


# ------------------------------------------------------------------ stream parity and offline fixtures


@executor("stream_pair")
async def stream_pair(ctx: ExecContext) -> list[Exchange]:
    """The same request non-streamed then streamed, after ``warmups`` identical warm-up requests so the prefix
    cache state is the same for both. ``stream_params`` are merged into the streamed request only (for
    ``tool_stream``). Labels: ``warmup``, ``nonstream``, ``stream``."""
    cfg = ctx.config
    out = [
        await ctx.client.send_canonical(ctx.request, stream=False, label="warmup")
        for _ in range(int(cfg.get("warmups", 1)))
    ]
    out.append(await ctx.client.send_canonical(ctx.request, stream=False, label="nonstream"))
    sreq = deep_merge(ctx.request, {"params": cfg.get("stream_params") or {}})
    out.append(await ctx.client.send_canonical(sreq, stream=True, label="stream"))
    return out


@executor("stream_fixture")
async def stream_fixture(ctx: ExecContext) -> list[Exchange]:
    """Offline (no request): replay a recorded stream through the real SSE parser and accumulator, cut at many
    places and with tool-call fragments of different indices interleaved differently. Returns ``nonstream`` (the
    fixture's non-stream body) and one ``split-<how>[+<order>]`` exchange per variation."""
    fx = ctx.config["fixture"]
    protocol = fx.get("protocol", "openai_chat")
    events = list(fx["events"])
    out = [streamfix.reference(protocol, fx["nonstream"])]
    orders: list[tuple[str, list[Any]]] = [("", events)]
    if ctx.config.get("interleave", True):
        pos = [i for i, e in enumerate(events) if isinstance(e, dict) and _frag_index(e) is not None]
        sub = [events[i] for i in pos]
        for name, alt in zip(("reverse", "roundrobin"), streamfix.interleavings(sub, _frag_index), strict=True):
            re_ordered = list(events)
            for i, e in zip(pos, alt, strict=True):
                re_ordered[i] = e
            orders.append((name, re_ordered))
    for oname, evs in orders:
        text = streamfix.to_sse(evs, protocol)
        for how in ctx.config.get("splits") or ["whole", "char", "token", "bytes3", "bytes7", "pairs"]:
            out.append(streamfix.reassemble(protocol, text, how, f"split-{how}" + (f"+{oname}" if oname else "")))
    return out


def _frag_index(event: dict[str, Any]) -> int | None:
    for ch in event.get("choices") or []:
        for tc in (ch.get("delta") or {}).get("tool_calls") or []:
            return tc.get("index", 0)
    return None


# ------------------------------------------------------------------ repeats and sweeps


@executor("repeat_prefix")
async def repeat_prefix(ctx: ExecContext) -> list[Exchange]:
    """Send a request with a long shared prefix (``prefix_tokens``, as the system message) twice, ``delay_s``
    apart (skipped against the in-process mock). Labels ``first`` and ``second``."""
    cfg = ctx.config
    req = dict(ctx.request)
    if cfg.get("prefix_tokens"):
        req["messages"] = [
            {"role": "system", "content": synth.prefix_text(int(cfg["prefix_tokens"]))},
            *req["messages"],
        ]
    first = await ctx.client.send_canonical(req, stream=ctx.stream, label="first")
    if ctx.target.provider != "mock":
        await asyncio.sleep(float(cfg.get("delay_s", 5.0)))
    return [first, await ctx.client.send_canonical(req, stream=ctx.stream, label="second")]


@executor("repeat_identical")
async def repeat_identical(ctx: ExecContext) -> list[Exchange]:
    """``n`` identical requests (determinism). Labels ``run0`` ... ."""
    return [
        await ctx.client.send_canonical(ctx.request, stream=ctx.stream, label=f"run{i}")
        for i in range(int(ctx.config.get("n", 3)))
    ]


@executor("param_sweep")
async def param_sweep(ctx: ExecContext) -> list[Exchange]:
    """One request per value of a body parameter (``param``, ``values``). Label is the value."""
    cfg = ctx.config
    out = []
    for v in cfg["values"]:
        req = deep_merge(ctx.request, {"params": {cfg["param"]: v}})
        out.append(await ctx.client.send_canonical(req, stream=ctx.stream, label=str(v)))
    return out


@executor("styled_replay")
async def styled_replay(ctx: ExecContext) -> list[Exchange]:
    """GLM-5#168: replay ``turns`` assistant turns whose reasoning is in ``style`` ('styled' = drifted
    ``=``/``the`` script, 'prose' = healthy control), then ask for the next step. One request per attempt."""
    cfg = ctx.config
    style = (cfg.get("styles") or {}).get(ctx.variant.id if ctx.variant else "", cfg.get("style", "styled"))
    hist = synth.replay_history(style, int(cfg.get("turns", 8)), int(cfg.get("words", 60)))
    req = {**ctx.request, "messages": hist, "tools": ctx.request.get("tools") or [synth.BASH_TOOL]}
    return [await ctx.client.send_canonical(req, stream=ctx.stream, label="final")]
