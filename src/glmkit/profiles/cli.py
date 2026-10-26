"""``glmkit profiles``: list and show endpoint profiles."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Endpoint profiles (targets). Keys are read from the env var each profile names.", no_args_is_help=True
)


@app.command("list")
def list_profiles() -> None:
    from glmkit.contracts.profile import load_profiles

    for p in load_profiles().values():
        key = f"${p.api_key_env}" if p.api_key_env else "-"
        state = "set" if p.api_key() else ("unset" if p.api_key_required else "optional")
        typer.echo(f"{p.name:20} {p.protocol:19} {p.endpoint_class:12} {p.model:22} {key} ({state})  {p.base_url}")


@app.command("show")
def show(name: str) -> None:
    import yaml

    from glmkit.contracts.profile import get_profile

    p = get_profile(name)
    typer.echo(yaml.safe_dump(p.model_dump(), sort_keys=False, allow_unicode=True).rstrip())
    typer.echo(f"url: {p.url}")
