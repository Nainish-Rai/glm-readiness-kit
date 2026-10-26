from __future__ import annotations

from typer.testing import CliRunner

from glmkit import __version__
from glmkit.cli import build_app


def test_help_lists_core_sub_apps() -> None:
    out = CliRunner().invoke(build_app(), ["--help"]).output
    for cmd in ("mock", "conform", "report", "profiles", "schema"):
        assert cmd in out


def test_version() -> None:
    assert CliRunner().invoke(build_app(), ["--version"]).output.strip() == __version__


def test_schema_export(tmp_path) -> None:
    res = CliRunner().invoke(build_app(), ["schema", "export", "-o", str(tmp_path)])
    assert res.exit_code == 0 and len(list(tmp_path.glob("*.schema.json"))) == 4
