"""``glmkit conform host``: the host-quality run (latency + fingerprint), with its own verdict."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer


def register(app: typer.Typer) -> None:
    @app.command("host")
    def host(
        target: Annotated[str, typer.Option("--target", "-t", help="Profile name or path to a profile YAML")],
        reference: Annotated[
            Path | None, typer.Option("--reference", "-r", help="Reference file from `conform reference record`")
        ] = None,
        label: Annotated[str, typer.Option(help="Host name shown on the Hosts tab (default: profile name)")] = "",
        cases: Annotated[
            list[str] | None, typer.Option("--cases", "-c", help="Cases to run (default LAT-*,FP-*); quote globs")
        ] = None,
        cases_dir: Annotated[list[Path] | None, typer.Option("--cases-dir")] = None,
        latency_n: Annotated[int | None, typer.Option("--latency-n", help="Samples per latency condition")] = None,
        samples: Annotated[int | None, typer.Option(help="Samples per prompt for FP-002/FP-003")] = None,
        out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
        mock_defect: Annotated[
            list[str] | None, typer.Option("--mock-defect", "-D", help="Plant a mock defect")
        ] = None,
        mock_chunk_delay: Annotated[float, typer.Option(help="Mock only: seconds between streamed chunks")] = 0.0,
        mock_first_token_delay: Annotated[float, typer.Option(help="Mock only: seconds before the first token")] = 0.0,
        timeout: Annotated[float, typer.Option(help="Per-request timeout (s)")] = 60.0,
        verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Print every check message")] = False,
    ) -> None:
        """Run the host-quality cases against TARGET and print the host-quality verdict.

        Never changes the correctness verdict of `conform run`. Exit code 1 when the host-quality verdict is `fail`.
        """
        from glmkit.client import MissingKeyError
        from glmkit.conform.cases import CaseError
        from glmkit.contracts.profile import get_profile
        from glmkit.hostq.engine import HOST_PATTERNS, HostOptions, run_host
        from glmkit.hostq.reference import ReferenceFileError, load_reference

        try:
            profile = get_profile(target)
        except KeyError as e:
            raise typer.BadParameter(str(e.args[0]), param_hint="--target") from e
        try:
            ref = load_reference(reference) if reference else None
        except ReferenceFileError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2) from e
        pats = [p.strip() for raw in cases or [] for p in raw.split(",") if p.strip()] or list(HOST_PATTERNS)
        opts = HostOptions(
            patterns=pats,
            case_roots=list(cases_dir) if cases_dir else None,
            reference=ref,
            label=label or profile.name,
            out_dir=out,
            latency_n=latency_n,
            samples=samples,
            mock_defects=[d for raw in mock_defect or [] for d in raw.split(",") if d],
            mock_chunk_delay_s=mock_chunk_delay,
            mock_first_token_delay_s=mock_first_token_delay,
            timeout_s=timeout,
        )
        try:
            outcome = asyncio.run(run_host(profile, opts))
        except (CaseError, MissingKeyError) as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2) from e
        env = outcome.envelope
        hq = env.data["host_quality"]
        t = env.targets[0]
        typer.echo(
            f"run {env.run_id}  host={hq['label']}  target={t.profile} ({t.protocol}, {t.endpoint_class}, {t.model})"
            + (f"  defects={','.join(opts.mock_defects)}" if opts.mock_defects else "")
            + (f"  reference={ref.reference_id}" if ref else "  reference=none")
        )
        marks = {"pass": "PASS", "flag": "FLAG", "not_evaluated": "N/A "}
        for g in hq["gates"]:
            typer.echo(f"  {marks[g['status']]}  {g['id']:8} {g['severity']:5}  {(g['message'] or '')[:150]}")
            if verbose:
                it = next((i for i in env.items if i.id == g["item"]), None)
                for c in it.checks if it else []:
                    typer.echo(f"          {c.status:9} {c.name}: {c.message[:200]}")
        typer.echo(f"host-quality verdict: {hq['verdict']}  ({hq['reason']})")
        if outcome.run_dir:
            typer.echo(f"results: {outcome.run_dir / 'result.json'}")
        if hq["verdict"] == "fail":
            raise typer.Exit(1)
