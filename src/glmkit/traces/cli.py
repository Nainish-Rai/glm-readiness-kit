"""``glmkit traces``: import customer production data as SwitchTrace v0 and summarise it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from glmkit.traces.registry import ImportContext, TaskMapper, adapters, get_adapter

app = typer.Typer(help="Trace importers: production data -> SwitchTrace v0.", no_args_is_help=True)

COMPONENT = "traces"


@app.command("sources")
def sources() -> None:
    """List the registered source kinds."""
    for kind, a in adapters().items():
        typer.echo(f"{kind:12} {a.title}  [{a.formats}]  adapter {a.version}")


@app.command("import")
def import_(
    path: Annotated[Path, typer.Argument(help="Source file (or folder of files for claude_code)")],
    from_: Annotated[str, typer.Option("--from", "-f", help="Source kind; see `glmkit traces sources`")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output SwitchTrace JSONL")] = Path("traces.jsonl"),
    redact: Annotated[bool, typer.Option("--redact", help="Redact emails, phones and API-key-like strings")] = False,
    redact_pattern: Annotated[
        list[str] | None, typer.Option("--redact-pattern", help="Extra regex to redact (repeatable)")
    ] = None,
    task_map: Annotated[
        Path | None, typer.Option("--task-map", help="YAML/JSON mapping of tags/models to task types")
    ] = None,
    results: Annotated[
        Path | None, typer.Option("--results", help="Also write a result envelope here (feeds the Traces tab)")
    ] = Path("results"),
    name: Annotated[str | None, typer.Option("--name", help="Label of the imported set (default: file name)")] = None,
) -> None:
    """Convert PATH (a production export of kind --from) into SwitchTrace v0 lines at --out."""
    from glmkit.contracts.envelope import ItemResult, ResultEnvelope, new_run_id
    from glmkit.results import add_artifact, run_dir, write_envelope
    from glmkit.traces.redaction import TraceRedactor
    from glmkit.traces.stats import compute_stats

    try:
        adapter = get_adapter(from_)
        mapper = TaskMapper.from_file(task_map)
        redactor = TraceRedactor(redact_pattern) if redact or redact_pattern else None
    except (KeyError, ValueError, OSError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    files = sorted(p for p in path.rglob("*.jsonl")) if path.is_dir() else [path]
    if not files or any(not f.exists() for f in files):
        typer.echo(f"error: no such file: {path}", err=True)
        raise typer.Exit(2)
    started = datetime.now(UTC)
    traces = []
    skipped: dict[str, int] = {}
    for f in files:
        ctx = ImportContext(path=f, mapper=mapper)
        for t in adapter.parse(ctx):
            traces.append(redactor.trace(t) if redactor else t)
        for k, v in ctx.skipped.items():
            skipped[k] = skipped.get(k, 0) + v
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(t.to_jsonl() + "\n" for t in traces), encoding="utf-8")
    stats = compute_stats(traces)
    typer.echo(
        f"{len(traces)} traces -> {out}  (replayable {stats['replayable']}, baseline-only {stats['baseline_only']})"
    )
    for k, v in sorted(skipped.items()):
        typer.echo(f"  skipped {v}: {k}")
    if redactor:
        typer.echo(f"  redacted: {dict(redactor.counts)}")
    if results is not None:
        env = ResultEnvelope(
            run_id=new_run_id(COMPONENT, started),
            component=COMPONENT,
            started_at=started,
            config={"from": from_, "input": str(path), "redact": bool(redactor), "task_map": str(task_map or "")},
            data={
                "set": name or path.name,
                "source": from_,
                "output": str(out),
                "stats": stats,
                "skipped": skipped,
                "redactions": dict(redactor.counts) if redactor else {},
            },
        )
        env.items.append(
            ItemResult(
                id=f"import:{name or path.name}",
                title=f"Import {from_} -> SwitchTrace v0",
                kind="trace_import",
                status="pass" if traces else "fail",
                reason=None if traces else "no traces produced",
                metrics={"traces": len(traces), "skipped": sum(skipped.values())},
            )
        )
        env.finalize()
        d = run_dir(results, env.run_id)
        (d / "traces.jsonl").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        add_artifact(env, "traces", d / "traces.jsonl", d)
        write_envelope(env, results)
    if not traces:
        raise typer.Exit(1)


@app.command("stats")
def stats_(
    paths: Annotated[list[Path], typer.Argument(help="SwitchTrace JSONL file(s)")],
    json_out: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """Counts by source, task type, replayable vs baseline-only, and token totals."""
    from glmkit.traces.stats import compute_stats, load_traces

    try:
        s = compute_stats(t for p in paths for t in load_traces(p))
    except (OSError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    if json_out:
        typer.echo(json.dumps(s, indent=2))
        return
    typer.echo(
        f"traces: {s['total']}  replayable: {s['replayable']}  baseline-only: {s['baseline_only']}"
        f"  redacted: {s['redacted']}"
    )
    typer.echo("by source:    " + ", ".join(f"{k}={v}" for k, v in s["by_source"].items()))
    typer.echo("by task type: " + ", ".join(f"{k}={v}" for k, v in s["by_task_type"].items()))
    typer.echo("tokens by source (input_total / cache_read / cache_write / output / reasoning):")
    for k, b in s["tokens_by_source"].items():
        typer.echo(
            f"  {k:12} {b['input_tokens_total']:>10} {b['cache_read_tokens']:>10} {b['cache_write_tokens']:>10} "
            f"{b['output_tokens_total']:>10} {b['reasoning_tokens']:>10}"
        )
    t = s["tokens_total"]
    typer.echo(
        f"  {'TOTAL':12} {t['input_tokens_total']:>10} {t['cache_read_tokens']:>10} {t['cache_write_tokens']:>10} "
        f"{t['output_tokens_total']:>10} {t['reasoning_tokens']:>10}"
    )
    if s["cost_usd_reported"] is not None:
        typer.echo(f"reported cost: ${s['cost_usd_reported']}")
