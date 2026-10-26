# ruff: noqa: E501
"""``glmkit evidence``: generate the deployment evidence pack (a checklist and evidence index, not legal advice)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer

from glmkit.evidence.disclaimer import DISCLAIMER

app = typer.Typer(
    help="Evidence pack: EU AI Act, GDPR, Singapore and export-control checklist for a GLM deployment.",
    no_args_is_help=True,
)


@app.command()
def build(
    deployment: Annotated[Path, typer.Option("--deployment", "-d", help="Deployment descriptor (YAML)")],
    results: Annotated[
        Path, typer.Option("--results", "-r", help="Results folder whose runs the pack links to")
    ] = Path("results"),
    out: Annotated[Path, typer.Option("--out", "-o", help="Pack folder")] = Path("evidence"),
    spdx: Annotated[
        bool, typer.Option("--spdx", help="Also emit an SPDX 3.0 AI export (not schema-validated)")
    ] = False,
    envelope: Annotated[
        bool,
        typer.Option(
            "--envelope/--no-envelope",
            help="Write a result envelope into --results so the dashboard Evidence tab shows it",
        ),
    ] = True,
    check: Annotated[
        bool, typer.Option("--check/--no-check", help="Run the Observatory token check on the pack pages")
    ] = True,
) -> None:
    """Build OUT/ with HTML and Markdown pages, the data-flow diagram, the AI-BOM and a manifest."""
    from glmkit.evidence.checklist import CatalogueError
    from glmkit.evidence.descriptor import DescriptorError
    from glmkit.evidence.pack import build_pack
    from glmkit.evidence.render import MissingDisclaimer

    try:
        res = build_pack(deployment, results, out, spdx=spdx, write_env=envelope, check=check)
    except (DescriptorError, CatalogueError, MissingDisclaimer) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    for w in res.warnings:
        typer.echo(f"warning: {w}", err=True)
    counts = {s: sum(1 for i in res.items if i.status == s) for s in ("met", "gap", "needs-counsel", "n/a")}
    typer.echo(
        f"built {res.out / 'index.html'}  items={len(res.items)}  " + "  ".join(f"{k}={v}" for k, v in counts.items())
    )
    typer.echo(
        f"runs linked: {len(res.runs)}  ai-bom: {'valid (CycloneDX 1.6, checked against 1.6 and 1.7)' if not res.bom_errors else 'INVALID'}"
    )
    if res.envelope_path:
        typer.echo(f"envelope: {res.envelope_path}  (run {res.run_id})")
    typer.echo(f"note: {DISCLAIMER}")
    for e in res.bom_errors:
        typer.echo(e, err=True)
    for f in res.findings:
        typer.echo(f)
    if check:
        typer.echo(f"token check: {len(res.findings)} finding(s)")
    if res.bom_errors or res.findings:
        raise typer.Exit(1)


@app.command()
def examples(
    out: Annotated[Path, typer.Option("--out", "-o", help="Folder to copy the four example descriptors to")] = Path(
        "deployments"
    ),
) -> None:
    """Copy the four example deployment descriptors (self-hosted, Mistral, Bedrock, Z.ai API)."""
    from glmkit.evidence.pack import example_descriptors

    out.mkdir(parents=True, exist_ok=True)
    for p in example_descriptors():
        shutil.copyfile(p, out / p.name)
        typer.echo(out / p.name)


@app.command("validate-bom")
def validate_bom(bom: Annotated[Path, typer.Argument(help="CycloneDX JSON file")]) -> None:
    """Validate a CycloneDX BOM against the vendored 1.6 and 1.7 schemas. Exit 1 when invalid."""
    from glmkit.evidence.aibom import validate_cyclonedx

    errors = validate_cyclonedx(json.loads(bom.read_text(encoding="utf-8")))
    for e in errors:
        typer.echo(e)
    typer.echo(f"{bom}: {'valid' if not errors else str(len(errors)) + ' error(s)'}")
    if errors:
        raise typer.Exit(1)
