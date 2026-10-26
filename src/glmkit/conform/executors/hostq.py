"""Executors of the host-quality cases (latency LAT-*, fingerprint FP-*). Each executor attempt produces labelled
exchanges (see ``glmkit.hostq.analysis``); for repeated probes the attempt index is the sample index.
"""

from __future__ import annotations

import asyncio
from typing import Any

from glmkit.conform.executors import ExecContext, executor
from glmkit.contracts.evidence import Exchange
from glmkit.hostq import probes


async def _send_all(
    ctx: ExecContext, jobs: list[tuple[str, dict[str, Any]]], *, stream: bool, concurrency: int = 1
) -> list[Exchange]:
    """Send ``(label, canonical request)`` jobs, at most ``concurrency`` at a time, preserving job order."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(label: str, req: dict[str, Any]) -> Exchange:
        async with sem:
            return await ctx.client.send_canonical(req, stream=stream, label=label)

    return list(await asyncio.gather(*(one(label, req) for label, req in jobs)))


def _synthetic_tools(n: int) -> list[dict[str, Any]]:
    return [
        {
            "name": f"tool_{i:02d}",
            "description": f"Synthetic tool number {i}; never needed for this request.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}, "flag": {"type": "boolean"}},
                "required": ["value"],
            },
        }
        for i in range(n)
    ]


@executor("latency_tools")
async def latency_tools(ctx: ExecContext) -> list[Exchange]:
    """LAT-001: stream one long cached prompt with 0 / 1 / 60 tools (``tool_choice`` from the case); one
    measurement per condition per attempt. Attempt 0 first sends an unmeasured warm-up to fill the prefix cache."""
    cfg = ctx.config
    prompt = probes.filler_text(probes.load_set("canaries"), int(cfg.get("prompt_tokens", 20000)))
    base = {**ctx.request, "messages": [{"role": "user", "content": f"{prompt}\n\nReply OK"}]}
    out: list[Exchange] = []

    def build(n_tools: int) -> dict[str, Any]:
        req = dict(base)
        if n_tools:
            req["tools"] = _synthetic_tools(n_tools)
        else:
            req.pop("tool_choice", None)
        return req

    if ctx.attempt == 0:
        out.append(await ctx.client.send_canonical(build(0), stream=True, label="warmup"))
    for n in cfg.get("tool_counts", [0, 1, 60]):
        name = "no_tools" if n == 0 else f"tools_{n}"
        out.append(await ctx.client.send_canonical(build(int(n)), stream=True, label=f"lat.{name}"))
    return out


@executor("latency_effort")
async def latency_effort(ctx: ExecContext) -> list[Exchange]:
    """LAT-002: stream the same short question at each reasoning effort; TTFT, decode and end-to-end tokens/s."""
    cfg = ctx.config
    prompt = cfg.get("prompt", "Explain in a few sentences why the sky is blue.")
    out: list[Exchange] = []
    for effort in cfg.get("efforts", ["low", "high", "max"]):
        req = {**ctx.request, "messages": [{"role": "user", "content": prompt}]}
        req["params"] = {**(req.get("params") or {}), "reasoning_effort": effort}
        out.append(await ctx.client.send_canonical(req, stream=True, label=f"eff.{effort}"))
    return out


@executor("model_identity")
async def model_identity(ctx: ExecContext) -> list[Exchange]:
    """FP-001: the model asked for must come back in the response; an unknown model must be refused."""
    known = {**ctx.request, "messages": [{"role": "user", "content": "Reply OK"}]}
    unknown = {**known, "model": ctx.config.get("unknown_model", "glm-9.9")}
    return [
        await ctx.client.send_canonical(known, stream=ctx.stream, label="id.known"),
        await ctx.client.send_canonical(unknown, stream=ctx.stream, label="id.unknown"),
    ]


def _probe_overrides(req: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    if "temperature" in cfg:
        req["temperature"] = cfg["temperature"]
    if "max_tokens" in cfg:
        req["max_tokens"] = cfg["max_tokens"]
    if "reasoning_effort" in cfg:
        req["params"] = {**req.get("params", {}), "reasoning_effort": cfg["reasoning_effort"]}
    return req


@executor("action_probe")
async def action_probe(ctx: ExecContext) -> list[Exchange]:
    """FP-002: one sample of every ambiguous agent prompt (tool choice); attempts are repeated samples."""
    cfg, ps = ctx.config, probes.load_set("action")
    n = min(int(cfg.get("prompts", 10_000)), len(ps.data["prompts"]))
    jobs = [
        (f"act.{ps.data['prompts'][i]['id']}", _probe_overrides(probes.action_request(ps, i), cfg)) for i in range(n)
    ]
    return await _send_all(ctx, jobs, stream=False, concurrency=int(cfg.get("concurrency", 4)))


@executor("text_probe")
async def text_probe(ctx: ExecContext) -> list[Exchange]:
    """FP-003: one sample of every short random-string prompt."""
    cfg, ps = ctx.config, probes.load_set("text")
    n = min(int(cfg.get("prompts", 10_000)), len(ps.data["prompts"]))
    jobs = [(f"txt.{ps.data['prompts'][i]['id']}", _probe_overrides(probes.text_request(ps, i), cfg)) for i in range(n)]
    return await _send_all(ctx, jobs, stream=False, concurrency=int(cfg.get("concurrency", 4)))


@executor("greedy_probe")
async def greedy_probe(ctx: ExecContext) -> list[Exchange]:
    """FP-004: the first prompts of the action set at temperature 0; identical across attempts on a stable host."""
    cfg, ps = ctx.config, probes.load_set("action")
    n = min(int(cfg.get("prompts", 8)), len(ps.data["prompts"]))
    jobs = [
        (f"grd.{ps.data['prompts'][i]['id']}", _probe_overrides(probes.action_request(ps, i, temperature=0), cfg))
        for i in range(n)
    ]
    return await _send_all(ctx, jobs, stream=False, concurrency=int(cfg.get("concurrency", 4)))


@executor("canary_probe")
async def canary_probe(ctx: ExecContext) -> list[Exchange]:
    """FP-005: BFCL-style tool calls, a math slice and long-context needles, one request each."""
    cfg, ps = ctx.config, probes.load_set("canaries")
    needle_tokens = cfg.get("needle_tokens")
    jobs = [
        (f"can.{c['id']}", probes.canary_request(ps, c, needle_tokens=needle_tokens)) for c in probes.canary_items(ps)
    ]
    return await _send_all(ctx, jobs, stream=False, concurrency=int(cfg.get("concurrency", 2)))
