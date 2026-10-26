"""``glmkit mock``: run the mock GLM server and list its defects."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from glmkit.mock import defects as defect_registry
from glmkit.mock.types import MockConfig

app = typer.Typer(help="Mock GLM server (OpenAI + Anthropic paths, named defects).", no_args_is_help=True)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8765,
    defect: Annotated[list[str] | None, typer.Option("--defect", "-d", help="Server-wide defect (repeatable)")] = None,
    endpoint_class: Annotated[
        str, typer.Option(help="Class for non-/coding/ paths: api | third_party | self_hosted")
    ] = "api",
    script: Annotated[Path | None, typer.Option(help="YAML file of scripted reply rules")] = None,
    chunk_chars: Annotated[int, typer.Option(help="Characters per streamed delta")] = 12,
    chunk_delay: Annotated[float, typer.Option(help="Seconds between streamed events")] = 0.0,
    require_key_env: Annotated[str | None, typer.Option(help="Env var holding the key clients must send")] = None,
) -> None:
    """Serve the mock until Ctrl-C. Per-request defects: header ``X-GLMKit-Defects: name1,name2``."""
    import uvicorn

    from glmkit.mock.app import create_app
    from glmkit.mock.server import load_rules

    cfg = MockConfig(
        endpoint_class=endpoint_class,
        defects=list(defect or []),
        rules=load_rules(script) if script else [],
        chunk_chars=chunk_chars,
        chunk_delay_s=chunk_delay,
        require_key=os.environ.get(require_key_env) if require_key_env else None,
    )
    try:
        asgi = create_app(cfg)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0])) from e
    typer.echo(f"mock GLM on http://{host}:{port}  class={endpoint_class}  defects={','.join(cfg.defects) or 'none'}")
    typer.echo(f"  OpenAI:    http://{host}:{port}/api/paas/v4/chat/completions")
    typer.echo(f"  Plan:      http://{host}:{port}/api/coding/paas/v4/chat/completions")
    typer.echo(f"  Anthropic: http://{host}:{port}/api/anthropic/v1/messages")
    uvicorn.run(asgi, host=host, port=port, log_level="warning", lifespan="off")


@app.command("defects")
def list_defects() -> None:
    """List the named defects the mock can plant."""
    for name, cls in defect_registry.discover().items():
        cases = f"  [cases: {', '.join(cls.cases)}]" if cls.cases else ""
        typer.echo(f"{name:28} {cls.title}{cases}")
