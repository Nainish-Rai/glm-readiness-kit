"""``glmkit eval``: migration eval engine."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="Migration eval: replay a golden set on targets, grade, report non-inferiority.", no_args_is_help=True
)


def _split(values: list[str] | None) -> list[str]:
    return [v.strip() for raw in values or [] for v in raw.split(",") if v.strip()]


def _fmt_ci(ci: list[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"


@app.command()
def run(
    set_path: Annotated[Path, typer.Option("--set", help="Golden set (YAML/JSON) or JSONL of items / SwitchTrace v0")],
    targets: Annotated[
        list[str],
        typer.Option(
            "--targets", "-t", help="Candidate profiles, 'name' or 'name@low|high|max' (repeatable, comma-separated)"
        ),
    ],
    baseline: Annotated[str, typer.Option("--baseline", "-b", help="Baseline (incumbent) profile")],
    judge: Annotated[
        str | None, typer.Option("--judge", help="Judge profile (required for judge-graded items)")
    ] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
    resume: Annotated[
        str | None, typer.Option("--resume", help="Continue run RUN_ID, replaying only missing calls")
    ] = None,
    run_id: Annotated[str | None, typer.Option("--run-id", help="Name the run (default: generated)")] = None,
    concurrency: Annotated[int, typer.Option(help="Calls in flight")] = 8,
    k: Annotated[int | None, typer.Option("-k", help="Replays per item and target (default: the set's k)")] = None,
    margin: Annotated[float, typer.Option(help="Non-inferiority margin in score points (0.05 = 5 points)")] = 0.05,
    min_items: Annotated[int, typer.Option(help="Fewer paired items than this is inconclusive")] = 20,
    seed: Annotated[int, typer.Option(help="Bootstrap seed")] = 0,
    bootstrap: Annotated[int, typer.Option("--bootstrap", help="Bootstrap resamples")] = 4000,
    budget_usd: Annotated[
        float | None, typer.Option("--budget-usd", help="Stop starting calls once this much is spent")
    ] = None,
    prices: Annotated[
        list[Path] | None,
        typer.Option(
            "--prices",
            help="Extra price entries (same format as glmkit/cost/prices.yaml) layered over the one price table",
        ),
    ] = None,
    profiles_dir: Annotated[
        list[Path] | None, typer.Option("--profiles-dir", help="Extra folder of endpoint profiles")
    ] = None,
    task_type: Annotated[list[str] | None, typer.Option("--task-type", help="Only these task types")] = None,
    max_items: Annotated[int | None, typer.Option(help="Only the first N items")] = None,
    timeout: Annotated[float, typer.Option(help="Per-request timeout (s)")] = 60.0,
    fail_on_inferior: Annotated[
        bool, typer.Option(help="Exit 1 when the verdict is fail (a candidate is inferior)")
    ] = False,
) -> None:
    """Replay SET on BASELINE and every target, grade, and print the paired verdict per task type."""
    from glmkit.eval.goldenset import GoldenError
    from glmkit.eval.runner import EvalError, EvalInterrupted, EvalOptions, run_eval

    if profiles_dir:
        existing = os.environ.get("GLMKIT_PROFILES_DIRS", "")
        os.environ["GLMKIT_PROFILES_DIRS"] = os.pathsep.join(
            [*[str(p) for p in profiles_dir], *([existing] if existing else [])]
        )
    opts = EvalOptions(
        set_path=set_path,
        targets=_split(targets),
        baseline=baseline,
        judge=judge,
        out_dir=out,
        run_id=resume or run_id,
        resume=resume is not None,
        concurrency=concurrency,
        k=k,
        margin=margin,
        min_items=min_items,
        seed=seed,
        n_boot=bootstrap,
        budget_usd=budget_usd,
        prices=list(prices or []),
        timeout_s=timeout,
        task_types=_split(task_type) or None,
        max_items=max_items,
    )
    try:
        outcome = asyncio.run(run_eval(opts))
    except (EvalError, GoldenError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    except (KeyboardInterrupt, EvalInterrupted):
        typer.echo(
            "interrupted: finished calls are saved; continue with --resume <run id> (see the run folder)", err=True
        )
        raise typer.Exit(130) from None
    env = outcome.envelope
    d = env.data
    typer.echo(
        f"run {env.run_id}  set={d['set']['name']} ({d['set']['items']} items)  baseline={d['baseline']}  margin={d['margin']}"  # noqa: E501
    )
    typer.echo(
        f"{'task type':14} {'target':26} {'n':>4} {'delta [95% CI]':>28}  {'verdict':13} {'cost x':>7}  recommendation"
    )
    for c in d["comparisons"]:
        q = c["quality"]
        if not q.get("n"):
            continue
        ratio = (c["cost"] or {}).get("ratio")
        typer.echo(
            f"{c['task_type']:14} {c['target']:26} {q['n']:>4} {q['delta']:+.3f} {_fmt_ci(q['ci']):>21}  {q['verdict']:13} "  # noqa: E501
            f"{(f'{ratio:.2f}') if ratio is not None else 'n/a':>7}  {c['recommendation']}"
        )
    for w in d["warnings"]:
        typer.echo(f"warning: {w}")
    b = d["budget"]
    typer.echo(
        f"spent ${b['spent_usd']:.4f}"
        + (f" of ${b['cap_usd']}" if b["cap_usd"] else "")
        + f"  (calls: {d['run']['new_calls']} new, {d['run']['resumed_calls']} resumed)"
    )
    typer.echo(f"verdict: {env.verdict}")
    typer.echo(f"results: {outcome.run_dir / 'result.json'}")
    if fail_on_inferior and env.verdict == "fail":
        raise typer.Exit(1)


@app.command()
def demo(
    out: Annotated[Path, typer.Option("--out", "-o", help="Folder to write the demo set and profiles into")] = Path(
        "eval-demo"
    ),
    scale: Annotated[int, typer.Option(help="Multiply the item counts")] = 1,
) -> None:
    """Write the mock-only demo (golden set, planted-quality profiles) and print how to run it."""
    from glmkit.eval.demo import write_demo

    paths = write_demo(out, scale)
    typer.echo(f"wrote {paths['set']} and {paths['profiles']}/")
    typer.echo(
        f"glmkit eval run --set {paths['set']} --baseline demo-incumbent --targets demo-glm-equal,demo-glm-worse,demo-glm@low,demo-glm@max "  # noqa: E501
        f"--judge demo-judge --profiles-dir {paths['profiles']} --seed 7"
    )


@app.command("prices")
def show_prices(prices: Annotated[list[Path] | None, typer.Option("--prices")] = None) -> None:
    """Print the price table in effect (version, per-1M prices, source URL and date per entry)."""
    from glmkit.eval.pricing import PriceTable

    t = PriceTable.load(*(prices or []))
    typer.echo(f"price table {t.version}")
    for e in t.describe()["entries"]:
        typer.echo(
            f"{e['model']:22} {e['provider']:14} in {e['input']:<7} cached {e['cached_input']:<7} out {e['output']:<7} {e['as_of']}  {e['source_url']}{'  (MOCK, illustrative)' if e['mock'] else ''}"  # noqa: E501
        )


@app.command("graders")
def list_graders() -> None:
    """List registered graders."""
    from glmkit.eval.graders import discover

    for name, fn in discover().items():
        typer.echo(f"{name:12} {(fn.__doc__ or '').strip().splitlines()[0] if fn.__doc__ else ''}")
