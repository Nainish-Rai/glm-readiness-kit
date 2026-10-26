"""``glmkit conform soak``: the opt-in, budget-capped soak cases (THINK-006 and any case tagged ``soak``).

Soak cases never run in ``glmkit conform run`` unless named exactly or ``--include-opt-in`` is given. This command
is the explicit switch: it selects the ``soak`` tag, loads opt-in cases, caps the decoded tokens, and writes its own
run, so a soak result is reported apart from the correctness verdict (soak cases are severity ``info``)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer

BUDGET_ENV = "GLMKIT_SOAK_BUDGET_TOKENS"
TURNS_ENV = "GLMKIT_SOAK_MAX_TURNS"


def register(app: typer.Typer) -> None:
    @app.command("soak")
    def soak(
        target: Annotated[str, typer.Option("--target", "-t", help="Profile name or path to a profile YAML")],
        budget: Annotated[int, typer.Option(help="Stop each soak after this many decoded tokens")] = 200_000,
        max_turns: Annotated[int | None, typer.Option(help="Stop after this many turns (default: the case's)")] = None,
        cases: Annotated[
            list[str] | None, typer.Option("--cases", "-c", help="Soak case globs (default: tag soak)")
        ] = None,
        mock_defect: Annotated[
            list[str] | None, typer.Option("--mock-defect", "-D", help="Plant a mock defect")
        ] = None,
        out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
        timeout: Annotated[float, typer.Option(help="Per-request timeout (s)")] = 60.0,
        verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Print every check")] = False,
    ) -> None:
        """Run the soak cases against TARGET within a token budget. Exit code 1 when the verdict is fail or error."""
        from glmkit.client import MissingKeyError
        from glmkit.conform.cases import CaseError
        from glmkit.conform.runner import RunOptions, run_conformance
        from glmkit.contracts.profile import get_profile

        try:
            profile = get_profile(target)
        except KeyError as e:
            raise typer.BadParameter(str(e.args[0]), param_hint="--target") from e
        os.environ[BUDGET_ENV] = str(budget)
        if max_turns is not None:
            os.environ[TURNS_ENV] = str(max_turns)
        opts = RunOptions(
            patterns=[p for raw in cases or [] for p in raw.split(",")] or ["soak"],
            mock_defects=[d for raw in mock_defect or [] for d in raw.split(",")],
            out_dir=out,
            timeout_s=timeout,
            include_opt_in=True,
        )
        try:
            outcome = asyncio.run(run_conformance(profile, opts))
        except (CaseError, MissingKeyError) as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2) from e
        env = outcome.envelope
        typer.echo(f"soak run {env.run_id}  target={profile.name}  budget={budget} decoded tokens")
        for it in env.items:
            turns = sum(1 for e in it.evidence if e.label.startswith("turn"))
            typer.echo(f"  {it.status.upper():5} {it.id:34} {turns} turns  {it.reason or ''}"[:200])
            if verbose:
                for c in it.checks:
                    typer.echo(f"          {c.status:9} {c.name}: {c.message[:120]}")
        typer.echo(f"verdict: {env.verdict}  (soak is reported apart from the correctness verdict)")
        if outcome.run_dir:
            typer.echo(f"results: {outcome.run_dir / 'result.json'}")
        if env.verdict in ("fail", "error"):
            raise typer.Exit(1)
