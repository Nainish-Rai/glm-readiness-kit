"""``glmkit cost``: explain what a GLM workload costs and why (price table, cache forensics, projection)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Cost and cache forensics over SwitchTrace v0 traces.", no_args_is_help=True)


def _pct(x: float | None) -> str:
    return "not logged" if x is None else f"{x * 100:.1f}%"


def _usd(x: float | None) -> str:
    return "not logged" if x is None else f"${x:,.4f}"


def _split(v: str | None) -> list[str] | None:
    out = [x.strip() for x in (v or "").split(",") if x.strip()]
    return out or None


@app.command()
def analyze(
    traces: Annotated[Path, typer.Argument(help="SwitchTrace v0 JSONL (one call per line)")],
    counts: Annotated[
        Path | None,
        typer.Option(help='JSONL of {"content_type"|"text", "glm_tokens", "incumbent_tokens"} for tokenizer inflation'),
    ] = None,
    models: Annotated[
        str | None, typer.Option(help="Comma-separated models to price (default glm-5.3,glm-5.3-flash)")
    ] = None,
    prices: Annotated[Path | None, typer.Option(help="Price table YAML (default: shipped, or $GLMKIT_PRICES)")] = None,
    break_threshold: Annotated[
        float, typer.Option(help="A call breaks the cache when it reuses less than this share of the prior prompt")
    ] = 0.85,
    idle_ttl: Annotated[
        float, typer.Option(help="Seconds of idle after which expiry is a suspect (assumed, HEURISTIC)")
    ] = 300.0,
    credits_per_usd: Annotated[
        float | None,
        typer.Option(help="HEURISTIC USD-to-Coding-Plan-credit conversion (credits per token are not published)"),
    ] = None,
    tasks_per_month: Annotated[float | None, typer.Option(help="Tasks per month, for the plan-fit check")] = None,
    disable: Annotated[str | None, typer.Option(help="Comma-separated detectors to switch off (proof runs)")] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
) -> None:
    """Explain the cost of TRACES: cache breaks and causes, verbosity, projection. Writes a result envelope."""
    from glmkit.contracts.envelope import ItemResult, ResultEnvelope, new_run_id
    from glmkit.cost.analyze import TraceFileError, read_counts, read_traces
    from glmkit.cost.analyze import analyze as run_analysis
    from glmkit.cost.prices import load_price_table
    from glmkit.results import write_envelope

    try:
        trs = read_traces(traces)
        cnt = read_counts(counts) if counts else None
        table = load_price_table(prices)
    except (TraceFileError, OSError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    if not trs:
        typer.echo(f"error: {traces} holds no trace records", err=True)
        raise typer.Exit(2)
    started = datetime.now(UTC)
    res = run_analysis(
        trs,
        table=table,
        counts=cnt,
        models=_split(models),
        threshold=break_threshold,
        idle_ttl_s=idle_ttl,
        credits_per_usd=credits_per_usd,
        tasks_per_month=tasks_per_month,
        disabled=set(_split(disable) or []) or None,
    )
    turns = res.pop("turns")
    env = ResultEnvelope(
        run_id=new_run_id("cost"),
        component="cost",
        started_at=started,
        config={
            "traces": str(traces),
            "counts": str(counts) if counts else None,
            **res["options"],
            "credits_per_usd": credits_per_usd,
            "tasks_per_month": tasks_per_month,
        },
    )
    for tr in res["traces"]:
        broke = tr["breaks"]
        env.items.append(
            ItemResult(
                id=tr["trace_id"],
                group=tr["task_type"],
                kind="cost_trace",
                title=f"{tr['model']} · {tr['calls']} calls",
                status="soft_fail" if broke else "pass",
                reason=(
                    f"first cache break at call {tr['first_break_seq']}: {tr['first_break_cause']}" if broke else None
                ),
                metrics={
                    "calls": tr["calls"],
                    "cache_share": tr["cache_share"],
                    "breaks": broke,
                    "lost_tokens": tr["lost_tokens"],
                },
                data={**tr, "turns": turns[tr["trace_id"]]},
            )
        )
    env.data = res
    env.finalize()
    path = write_envelope(env, out)
    t = res["totals"]
    typer.echo(
        f"{t['traces']} traces, {t['calls']} calls · cache share {_pct(t['cache_share'])} · "
        f"{t['breaks']} break(s) in {t['traces_with_breaks']} trace(s) · lost {t['lost_cache_tokens']:,} cached tokens "
        f"(~{_usd(t['wasted_usd'])} at Z.ai list price, HEURISTIC)"
    )
    for c in res["causes"]:
        typer.echo(
            f"  {c['cause']:26} {c['breaks']:3} break(s)  {c['lost_tokens']:>9,} tokens  {c['label']} (heuristic)"
        )
    for tr in res["traces"]:
        if tr["breaks"]:
            typer.echo(f"  {tr['trace_id']:32} first break at call {tr['first_break_seq']}: {tr['first_break_cause']}")
    for p in res["projection"]["payg"]:
        typer.echo(
            f"  {p['host']:20} {p['model']:15} {_usd(p['usd'])}"
            + (f"  (upper bound {_usd(p['usd_upper_bound'])})" if p["usd"] is None and p.get("usd_upper_bound") else "")
        )
    typer.echo(f"result: {path}")


@app.command()
def demo(
    out: Annotated[Path, typer.Option("--out", "-o", help="Folder for traces.jsonl and counts.jsonl")] = Path(
        "cost-demo"
    ),
) -> None:
    """Write synthetic traces with planted cache breaks (every scenario) plus synthetic token counts."""
    import json

    from glmkit.cost.synth import SCENARIOS, demo_token_counts, demo_traces

    out.mkdir(parents=True, exist_ok=True)
    (out / "traces.jsonl").write_text("".join(t.to_jsonl() + "\n" for t in demo_traces()), encoding="utf-8")
    (out / "counts.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in demo_token_counts()), encoding="utf-8"
    )
    typer.echo(
        f"wrote {out / 'traces.jsonl'} ({len(SCENARIOS)} planted scenarios) and {out / 'counts.jsonl'} (synthetic)"
    )


@app.command("prices")
def prices_cmd(
    model: Annotated[str | None, typer.Option(help="Only this model")] = None,
    path: Annotated[Path | None, typer.Option("--prices", help="Price table YAML")] = None,
) -> None:
    """Show the price table (USD per 1M tokens) with source and retrieval date."""
    from glmkit.cost.prices import load_price_table

    table = load_price_table(path)
    for e in table.prices:
        if model and e.model != model:
            continue
        f = lambda v: "not logged" if v is None else f"{v:g}"  # noqa: E731
        typer.echo(
            f"{e.host:20} {e.model:16} in {f(e.input):>10} cached {f(e.cached_input):>10} out {f(e.output):>10}  "
            f"{e.retrieved}  {e.source}"
        )
    if not model:
        cp = table.coding_plan
        for t in cp.tiers:
            typer.echo(
                f"coding-plan {t.name:5} ${t.usd_per_month:g}/mo  5h {t.credits_5h:,}  weekly {t.credits_weekly:,}  "
                f"credits {t.retrieved}  {t.source}"
            )
        typer.echo(
            f"coding-plan peak {cp.peak_window.description}; off-peak x{cp.off_peak_multiplier.value:g} (published); "
            f"peak x{cp.reported_peak_multiplier.value:g} (user reports, HEURISTIC)"
        )
