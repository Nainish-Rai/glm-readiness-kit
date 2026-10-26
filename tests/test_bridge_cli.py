from __future__ import annotations

from typer.testing import CliRunner

from glmkit.cli import build_app


def test_bridge_cli_registered_and_validates() -> None:
    r = CliRunner()
    app = build_app()
    assert "serve" in r.invoke(app, ["bridge", "--help"]).output
    assert "cerebras" in r.invoke(app, ["bridge", "flavors"]).output
    bad = r.invoke(app, ["bridge", "serve", "--upstream", "no-such-profile"])
    assert bad.exit_code != 0
    # Anthropic-protocol profiles are served too since issue 07; a bad search backend is rejected before anything starts
    assert "--search-backend" in r.invoke(app, ["bridge", "serve", "--help"]).output
    bad_backend = r.invoke(app, ["bridge", "serve", "--upstream", "mock-anthropic", "--search-backend", "nope"])
    assert bad_backend.exit_code != 0
