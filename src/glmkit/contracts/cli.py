"""``glmkit schema``: export the JSON Schemas of the shared contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

COMMAND = "schema"
app = typer.Typer(help="JSON Schemas of the shared contracts (envelope, case, trace, profile).", no_args_is_help=True)


@app.command("export")
def export(out: Annotated[Path, typer.Option("--out", "-o", help="Output folder")] = Path("schemas")) -> None:
    """Write <name>.schema.json for every contract."""
    from glmkit.contracts.schemas import export_all

    for p in export_all(out):
        typer.echo(str(p))
