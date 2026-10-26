"""``glmkit deploy``: size a self-hosted GLM deployment, validate the formulas, check a deployment's health."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(help="Deploy: MoE sizing calculator, reference deployment checks.", no_args_is_help=True)


def _fmt(n: float | None, d: int = 0) -> str:
    return "not logged" if n is None else f"{n:,.{d}f}"


@app.command()
def size(
    model: Annotated[str, typer.Option(help="Model id")] = "glm-5.3",
    precision: Annotated[str, typer.Option(help="Weights: fp8 | bf16 | nvfp4")] = "fp8",
    kv_dtype: Annotated[str, typer.Option(help="KV cache dtype: fp8 | bf16")] = "fp8",
    gpu: Annotated[str, typer.Option(help="GPU key, see `glmkit deploy catalog`")] = "h200",
    gpus: Annotated[int, typer.Option(help="GPUs in the tensor-parallel group")] = 8,
    context: Annotated[str, typer.Option(help="Max model length, e.g. 128k, 1m, 131072")] = "128k",
    concurrency: Annotated[int, typer.Option(help="Concurrent sequences")] = 64,
    avg_input: Annotated[str, typer.Option(help="Average input tokens per request")] = "8k",
    avg_output: Annotated[str, typer.Option(help="Average output tokens per request")] = "1k",
    prefix_reuse: Annotated[float, typer.Option(help="Share of input tokens served from the prefix cache")] = 0.0,
    api: Annotated[str, typer.Option(help="API price list to compare with")] = "zai",
    utilisation: Annotated[float | None, typer.Option(help="Average GPU utilisation 0-1 (default 0.5)")] = None,
    mtp_accept: Annotated[float, typer.Option(help="Tokens per step with MTP (1 = off, ~2.7-3.2 measured)")] = 1.0,
    effort_multiplier: Annotated[float, typer.Option(help="Output-token multiplier for reasoning effort")] = 1.0,
    dcp: Annotated[int, typer.Option(help="Decode context parallel size (1 = KV replicated across TP ranks)")] = 1,
    include_indexer: Annotated[bool, typer.Option(help="Add the DSA indexer key cache (engine specific)")] = False,
    out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
    write: Annotated[bool, typer.Option(help="Write a result envelope for the dashboard")] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full result as JSON")] = False,
) -> None:
    """Weights, MLA KV per token, KV capacity, MoE bandwidth, throughput, $/M tokens vs the API and break-even.

    Exit code 1 when the configuration is infeasible. Throughput and cost are heuristic estimates.
    """
    from glmkit.deploy import sizing
    from glmkit.deploy.run import sizing_envelope
    from glmkit.redact import Redactor
    from glmkit.results import write_envelope

    try:
        inp = sizing.Inputs(
            model=model,
            precision=precision,
            kv_dtype=kv_dtype,
            gpu=gpu,
            n_gpus=gpus,
            context=sizing.parse_tokens(context),
            concurrency=concurrency,
            avg_input=sizing.parse_tokens(avg_input),
            avg_output=sizing.parse_tokens(avg_output),
            prefix_reuse=prefix_reuse,
            api=api,
            utilisation=utilisation,
            mtp_accept=mtp_accept,
            effort_multiplier=effort_multiplier,
            dcp=dcp,
            include_indexer=include_indexer,
        )
        res = sizing.size(inp)
    except sizing.SizingError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    validation = sizing.validate()
    if write:
        env = sizing_envelope(res, validation)
        path = write_envelope(env, out, Redactor([]))
        res["result_path"] = str(path)
    if as_json:
        typer.echo(json.dumps(res, indent=2))
    else:
        _print(res)
    if not res["feasibility"]["feasible"]:
        raise typer.Exit(1)


def _print(r: dict[str, Any]) -> None:
    i, w, m, kv, f = r["inputs"], r["weights"], r["memory"], r["kv"], r["feasibility"]
    typer.echo(
        f"{i['model']} {i['precision']} on {i['n_gpus']}x {i['gpu']}  "
        f"context {i['context']:,}  concurrency {i['concurrency']}"
    )
    typer.echo(f"  weights        {_fmt(w['total_gb'], 1)} GB total, {_fmt(w['per_gpu_gb'], 1)} GB per GPU")
    typer.echo(
        f"  per GPU        {_fmt(m['hbm_gb'])} GB HBM, {_fmt(m['usable_gb'], 1)} usable, "
        f"KV free {_fmt(m['kv_free_per_gpu_gb'], 1)} GB"
    )
    typer.echo(
        f"  KV             {_fmt(kv['bytes_per_token'])} B/token ({kv['bytes_per_token'] / 1000:.1f} KB), "
        f"{_fmt(kv['max_tokens'])} tokens, max concurrency {_fmt(kv['max_concurrency'])} "
        f"at {_fmt(kv['tokens_per_seq'])} tokens/seq"
    )
    if r["moe"]:
        mo, p = r["moe"], r["perf"]
        typer.echo(
            f"  MoE            batch {mo['batch']}: {mo['experts_touched_per_layer']:.1f}/256 experts per layer "
            f"({mo['touched_fraction']:.0%}), {_fmt(mo['bytes_read_per_step_gb'])} GB read per step"
        )
        typer.echo(
            f"  throughput     {_fmt(p['decode_tok_s'])} tok/s decode ({p['per_user_tok_s']:.1f}/user), "
            f"{_fmt(p['prefill_tok_s'])} tok/s prefill  [heuristic: eta {p['eta']:.3f}, skew {p['routing_skew']:.3f}]"
        )
        c = r["cost"]
        typer.echo(
            f"  cost           ${c['self_usd_m']:.2f}/M self-hosted at {c['utilisation']:.0%} utilisation vs "
            f"${c['api_usd_m']:.2f}/M API ({i['api']})"
        )
        be = c["breakeven_utilisation"]
        typer.echo(
            f"  break-even     {be:.0%} utilisation"
            if c["breakeven_reachable"]
            else f"  break-even     never at this config (needs {be:.0%} utilisation)"
        )
    typer.echo("  feasibility    " + ("FEASIBLE" if f["feasible"] else "INFEASIBLE"))
    for reason in f["reasons"]:
        typer.echo(f"    - {reason}")
    if r.get("result_path"):
        typer.echo(f"results: {r['result_path']}")


def _coerce(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


@app.command()
def validate(
    config: Annotated[Path | None, typer.Option(help="Model config JSON (default: bundled GLM-5.3 snapshot)")] = None,
    manifest: Annotated[Path | None, typer.Option(help="Repo file listing JSON (default: bundled snapshot)")] = None,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Override a config key, e.g. kv_lora_rank=1024 (repeatable)")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recompute 44.9 KB/token (llm-d) and 755.7 GB FP8 weights from the config and file listing."""
    from glmkit.deploy import sizing

    cfgdoc = json.loads(config.read_text()) if config else sizing.load_config()
    for kv in set_ or []:
        key, _, val = kv.partition("=")
        if key not in cfgdoc["config"]:
            raise typer.BadParameter(f"unknown config key {key!r}", param_hint="--set")
        cfgdoc["config"][key] = _coerce(val)
    mf = json.loads(manifest.read_text()) if manifest else None
    results = sizing.validate(cfgdoc, mf)
    if as_json:
        typer.echo(json.dumps(results, indent=2))
    else:
        for v in results:
            typer.echo(
                f"{'PASS' if v['ok'] else 'FAIL'}  {v['name']}: computed {v['computed']:,.1f} vs reference "
                f"{v['reference']:,.1f} (error {v['error']:.3%}, tolerance {v['tolerance']:.1%})  [{v['formula']}]"
            )
    if not all(v["ok"] for v in results):
        raise typer.Exit(1)


@app.command()
def catalog() -> None:
    """GPU prices, API prices and calibration points, each with its source and date."""
    from glmkit.deploy.sizing import load_catalog

    c = load_catalog()
    typer.echo("GPUs (USD per GPU-hour)")
    for k, g in c["gpus"].items():
        typer.echo(
            f"  {k:16} ${g['usd_hr']:.2f}  {g['hbm_gb']} GB  {g['price_source']['date']}  {g['price_source']['url']}"
        )
    typer.echo("API list prices (USD per 1M tokens: in / cached / out)")
    for k, a in c["apis"].items():
        cached = "-" if a["cached_usd_m"] is None else f"{a['cached_usd_m']}"
        typer.echo(
            f"  {k:16} {a['in_usd_m']} / {cached} / {a['out_usd_m']}  {a['source']['date']}  {a['source']['url']}"
        )
    typer.echo("Calibration points")
    for k, p in c["calibration"].items():
        typer.echo(f"  {k:16} {p['label']}  {p['date']}  {p['url']}")


@app.command()
def health(
    target: Annotated[
        str, typer.Option("--target", "-t", help="Profile name (e.g. vllm-local, sglang-local) or YAML path")
    ],
    wait: Annotated[float, typer.Option(help="Seconds to wait for the endpoint to answer /models")] = 0.0,
    cases: Annotated[list[str] | None, typer.Option("--cases", "-c", help="Case globs (default WIRE-*)")] = None,
    mock_defect: Annotated[list[str] | None, typer.Option("--mock-defect", "-D", help="Plant a mock defect")] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("results"),
) -> None:
    """Readiness: wait for the endpoint, then run the WIRE conformance cases against it. Exit 1 on fail/error."""
    from glmkit.client import MissingKeyError
    from glmkit.contracts.profile import get_profile
    from glmkit.deploy.health import run_health, wait_ready

    try:
        profile = get_profile(target)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--target") from e
    ready, detail = wait_ready(profile, wait)
    typer.echo(f"endpoint: {'ready' if ready else 'NOT READY'} ({detail})")
    if not ready:
        raise typer.Exit(1)
    try:
        outcome = asyncio.run(run_health(profile, out, cases, mock_defect))
    except MissingKeyError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    env = outcome.envelope
    for it in env.items:
        typer.echo(
            f"  {it.status.upper():9} {it.id}" + (f"  {it.reason[:120]}" if it.status != "pass" and it.reason else "")
        )
    typer.echo(f"verdict: {env.verdict}")
    if outcome.run_dir:
        typer.echo(f"results: {outcome.run_dir / 'result.json'}")
    if env.verdict in ("fail", "error"):
        raise typer.Exit(1)
