"""``glmkit conform reference record|show``: store and inspect a reference fingerprint for host-quality runs."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated

import typer

ref_app = typer.Typer(help="Reference fingerprints for host-quality comparison.", no_args_is_help=True)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "reference"


@ref_app.command("record")
def record(
    target: Annotated[str, typer.Option("--target", "-t", help="Trusted target: official API or self-hosted FP8")],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Reference file (default references/<label>.json)")
    ] = None,
    label: Annotated[str, typer.Option(help="Name stored in the reference (default: the profile name)")] = "",
    samples: Annotated[
        int | None, typer.Option(help="Samples per prompt for FP-002/FP-003 (default: the case's)")
    ] = None,
    cases_dir: Annotated[list[Path] | None, typer.Option("--cases-dir")] = None,
    mock_defect: Annotated[list[str] | None, typer.Option("--mock-defect", "-D", help="Plant a mock defect")] = None,
    timeout: Annotated[float, typer.Option(help="Per-request timeout (s)")] = 60.0,
) -> None:
    """Sample TARGET (action histogram, text samples, greedy self-agreement, canaries) and store the reference."""
    from glmkit.client import MissingKeyError
    from glmkit.conform.cases import CaseError
    from glmkit.contracts.profile import get_profile
    from glmkit.hostq.engine import RECORD_PATTERNS, HostOptions, record_reference, run_host

    try:
        profile = get_profile(target)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--target") from e
    opts = HostOptions(
        patterns=list(RECORD_PATTERNS),
        case_roots=list(cases_dir) if cases_dir else None,
        label=label or profile.name,
        samples=samples,
        mock_defects=[d for raw in mock_defect or [] for d in raw.split(",") if d],
        timeout_s=timeout,
        write=False,
    )
    try:
        outcome = asyncio.run(run_host(profile, opts))
    except (CaseError, MissingKeyError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    ref = record_reference(outcome, opts.label, samples)
    missing = [k for k in ("action", "text", "self_agreement", "canary") if not getattr(ref, k)]
    if missing:
        typer.echo(f"error: no usable data for {', '.join(missing)}; not writing a reference", err=True)
        raise typer.Exit(1)
    path = ref.save(out or Path("references") / f"{_slug(opts.label)}.json")
    typer.echo(f"reference {ref.reference_id} ({ref.label}) -> {path}")
    typer.echo(
        f"  action samples {len(ref.action['samples'])}, text samples {len(ref.text['samples'])}, "
        f"self-agreement {ref.self_agreement.get('rate')}, canaries {ref.canary.get('correct')}/{ref.canary.get('n')}"
    )
    typer.echo(f"  probe sets {ref.probe_sets}")


@ref_app.command("show")
def show(path: Annotated[Path, typer.Argument(help="Reference file")]) -> None:
    """Print a reference's id, origin and probe-set ids; fails when it is stale or was edited."""
    from glmkit.hostq.reference import ReferenceFileError, load_reference

    try:
        ref = load_reference(path)
    except ReferenceFileError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    for k, v in ref.summary().items():
        typer.echo(f"{k:12} {v}")


def register(app: typer.Typer) -> None:
    app.add_typer(ref_app, name="reference")
