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
    anth = r.invoke(app, ["bridge", "serve", "--upstream", "mock-anthropic"])
    assert anth.exit_code != 0
