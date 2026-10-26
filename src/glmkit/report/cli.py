"""``glmkit report``: build the static dashboard and run the Observatory token check."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Static dashboard from result envelopes (opens from disk).", no_args_is_help=True)


@app.command()
def build(
    results: Annotated[Path, typer.Argument(help="Results folder (or one result.json)")] = Path("results"),
    out: Annotated[Path, typer.Option("--out", "-o", help="Site folder")] = Path("site"),
    check: Annotated[bool, typer.Option(help="Run the Observatory token check on the built site")] = True,
) -> None:
    """Build SITE/index.html with one tab per page module. Exit 1 if the token check finds anything."""
    from glmkit.report.builder import BuildError, build_site

    try:
        res = build_site(results, out, check=check)
    except BuildError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    for w in res.warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(f"built {res.index}  tabs={','.join(res.pages)}  runs={res.runs}")
    if check:
        for f in res.findings:
            typer.echo(f)
        typer.echo(f"token check: {len(res.findings)} finding(s)")
        if res.findings:
            raise typer.Exit(1)


@app.command("check")
def check_cmd(site: Annotated[Path, typer.Argument(help="Built site folder")] = Path("site")) -> None:
    """Run the vendored Observatory check_tokens.py over a built site."""
    from glmkit.report.builder import BuildError, check_site

    try:
        findings = check_site(site)
    except BuildError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    for f in findings:
        typer.echo(f)
    typer.echo(f"token check: {len(findings)} finding(s)")
    if findings:
        raise typer.Exit(1)
