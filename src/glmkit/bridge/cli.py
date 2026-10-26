"""``glmkit bridge``: serve the OpenAI-compatible bridge proxy."""

from __future__ import annotations

import logging
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import typer

from glmkit.bridge.core.upstream import UPSTREAMS, get_upstream, upstream_for_endpoint
from glmkit.redact import RedactingFilter, Redactor

app = typer.Typer(
    help="glm-bridge: fix known GLM wire problems in front of any OpenAI-compatible client.", no_args_is_help=True
)


@app.command()
def serve(
    upstream: Annotated[
        str, typer.Option("--upstream", "-u", help="Endpoint profile to forward to (glmkit profiles list)")
    ],
    flavor: Annotated[
        str | None, typer.Option(help=f"Upstream behaviour: {', '.join(UPSTREAMS)} (default: from the profile)")
    ] = None,
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8787,
    out: Annotated[
        Path, typer.Option(help="Results folder; the session envelope lands in <out>/<run_id>/result.json")
    ] = Path("results"),
    log_content: Annotated[
        bool, typer.Option("--log-content", help="Also log message content (off by default)")
    ] = False,
    mock_defect: Annotated[
        list[str] | None, typer.Option("--mock-defect", help="spawn_mock profiles: plant this mock defect (repeatable)")
    ] = None,
    timeout: Annotated[float, typer.Option(help="Upstream timeout in seconds")] = 120.0,
) -> None:
    """Serve until Ctrl-C. Point an OpenAI SDK at http://HOST:PORT/v1 (any key; the profile's key env var wins)."""
    import uvicorn

    from glmkit.bridge.proxy import ProxyConfig, create_app
    from glmkit.contracts.profile import get_profile
    from glmkit.mock import MockConfig, MockServer

    try:
        profile = get_profile(upstream)
        up = (
            get_upstream(flavor)
            if flavor
            else upstream_for_endpoint(profile.provider, profile.endpoint_class, profile.base_url)
        )
    except (KeyError, ValueError) as e:
        raise typer.BadParameter(str(e.args[0] if e.args else e)) from e
    if profile.protocol != "openai_chat":
        raise typer.BadParameter(
            f"profile {upstream!r} speaks {profile.protocol}; this proxy forwards to openai_chat upstreams"
        )
    with ExitStack() as stack:
        if profile.spawn_mock or "{mock}" in profile.base_url:
            srv = stack.enter_context(
                MockServer(MockConfig(defects=list(mock_defect or []), endpoint_class=profile.endpoint_class))
            )
            profile = profile.model_copy(
                update={"base_url": profile.base_url.replace("{mock}", srv.url), "spawn_mock": False}
            )
            typer.echo(f"mock GLM on {srv.url} defects={','.join(mock_defect or []) or 'none'}")
        key = profile.api_key()
        if profile.api_key_required and not key:
            raise typer.BadParameter(
                f"{profile.api_key_env} is not set (the upstream key is read from the environment)"
            )
        cfg = ProxyConfig(profile=profile, upstream=up, out_dir=out, log_content=log_content, timeout_s=timeout)
        asgi = create_app(cfg)
        rec = asgi.state.recorder
        run_dir = out / rec.env.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(RedactingFilter(Redactor([key])))
        blog = logging.getLogger("glmkit.bridge")
        blog.addHandler(handler)
        blog.addHandler(logging.StreamHandler(sys.stderr))
        blog.setLevel(logging.INFO)
        typer.echo(f"glm-bridge on http://{host}:{port}/v1  upstream={profile.name} ({profile.url})  flavor={up.name}")
        typer.echo(f"session: {run_dir / 'result.json'}  content logging: {'ON' if log_content else 'off'}")
        uvicorn.run(asgi, host=host, port=port, log_level="warning")


@app.command("flavors")
def flavors() -> None:
    """List the upstream behaviour profiles."""
    for u in UPSTREAMS.values():
        typer.echo(f"{u.name:10} {u.title}")
