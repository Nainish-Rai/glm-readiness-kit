"""``glmkit conform``: run conformance cases against a target and write a result envelope."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from glmkit.plugins import import_submodules

app = typer.Typer(help="Conformance: run named cases against a target profile.", no_args_is_help=True)

STATUS_LABEL = {
    "pass": "PASS",
    "fail": "FAIL",
    "soft_fail": "SOFT",
    "skip": "SKIP",
    "error": "ERR ",
    "throttled": "THRT",
}


def _split(values: list[str] | None) -> list[str] | None:
    out = [v.strip() for raw in values or [] for v in raw.split(",") if v.strip()]
    return out or None


@app.command()
def run(
    target: Annotated[str, typer.Option("--target", "-t", help="Profile name or path to a profile YAML")],
    cases: Annotated[
        list[str] | None,
        typer.Option(
            "--cases", "-c", help="Glob on case id/category/path (repeatable, comma-separated); quote it in zsh"
        ),
    ] = None,
    cases_dir: Annotated[
        list[Path] | None, typer.Option("--cases-dir", help="Case root(s) instead of the defaults")
    ] = None,
    mode: Annotated[
        list[str] | None, typer.Option("--mode", help="nonstream | stream (default: each case's modes)")
    ] = None,
    mock_defect: Annotated[
        list[str] | None, typer.Option("--mock-defect", "-D", help="Plant a mock defect (repeatable)")
    ] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
    timeout: Annotated[float, typer.Option(help="Per-request timeout (s)")] = 60.0,
    stream_timeout: Annotated[
        float, typer.Option(help="Whole-stream deadline (s); a hanging stream fails here")
    ] = 120.0,
    include_opt_in: Annotated[bool, typer.Option(help="Also run opt-in (soak / costly) cases")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Print every check")] = False,
) -> None:
    """Run cases against TARGET. Exit code 1 when the verdict is fail or error."""
    from glmkit.client import MissingKeyError
    from glmkit.conform.cases import CaseError
    from glmkit.conform.runner import RunOptions, run_conformance
    from glmkit.contracts.profile import get_profile

    try:
        profile = get_profile(target)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--target") from e
    opts = RunOptions(
        patterns=_split(cases),
        case_roots=list(cases_dir) if cases_dir else None,
        modes=_split(mode),
        mock_defects=_split(mock_defect) or [],
        out_dir=out,
        timeout_s=timeout,
        stream_timeout_s=stream_timeout,
        include_opt_in=include_opt_in,
    )
    try:
        outcome = asyncio.run(run_conformance(profile, opts))
    except (CaseError, MissingKeyError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    env = outcome.envelope
    t = env.targets[0]
    typer.echo(
        f"run {env.run_id}  target={t.profile} ({t.protocol}, {t.endpoint_class}, {t.model})"
        + (f"  defects={','.join(opts.mock_defects)}" if opts.mock_defects else "")
    )
    for it in env.items:
        line = f"  {STATUS_LABEL[it.status]}  {it.id:34} {it.severity or '':8}"
        if it.status != "pass" and it.reason:
            line += f"  {it.reason[:140]}"
        typer.echo(line)
        if verbose:
            for c in it.checks:
                typer.echo(f"          {c.status:9} {c.name}: {c.message[:120]}")
    s = env.summary
    typer.echo(
        f"verdict: {env.verdict}  ({s.passed} passed, {s.failed} failed, {s.soft_failed} soft, "
        f"{s.skipped} skipped, {s.errored} errors)"
    )
    if outcome.run_dir:
        typer.echo(f"results: {outcome.run_dir / 'result.json'}")
    if env.verdict in ("fail", "error"):
        raise typer.Exit(1)


@app.command("list")
def list_cases(
    cases: Annotated[list[str] | None, typer.Option("--cases", "-c", help="Glob filter")] = None,
    cases_dir: Annotated[list[Path] | None, typer.Option("--cases-dir")] = None,
) -> None:
    """List cases with severity, kind, modes and source."""
    from glmkit.conform.cases import load_cases

    for lc in load_cases(list(cases_dir) if cases_dir else None, _split(cases), include_opt_in=True):
        c = lc.case
        src = ", ".join(c.source.issues) or "-"
        typer.echo(f"{c.id:10} {c.severity:8} {c.kind:13} {','.join(c.modes):17} {c.title}  [{src}]")


@app.command("checks")
def list_checks() -> None:
    """List registered checks (named oracles)."""
    from glmkit.conform.checks import discover

    for name, d in discover().items():
        typer.echo(f"{name:28} {'aggregate ' if d.aggregate else ''}{d.description}  ({d.module})")


@app.command("executors")
def list_executors() -> None:
    """List registered case executors."""
    from glmkit.conform.executors import discover

    for name, fn in sorted(discover().items()):
        typer.echo(f"{name:16} {fn.__module__}")


for _mod in import_submodules("glmkit.conform.commands"):
    if callable(getattr(_mod, "register", None)):
        _mod.register(app)
