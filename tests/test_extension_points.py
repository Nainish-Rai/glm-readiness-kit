"""Acceptance: adding a tab, a CLI sub-app, a defect, a check, an executor or a case folder needs no edit to the
builder, runner, server or CLI core. A dummy plugin outside the package registers one of each, and a dummy case
folder is added through GLMKIT_CASES_DIRS; everything is picked up by discovery alone."""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Iterator

import pytest
import yaml
from typer.testing import CliRunner

from glmkit.cli import build_app

PLUGIN = '''
import typer
from glmkit.conform.checks import check, fail, ok
from glmkit.conform.executors import executor
from glmkit.mock.defects import Defect, register_defect
from glmkit.report.registry import Page, register_page


def register_cli(root):
    sub = typer.Typer(help="Dummy slice sub-app")

    @sub.command()
    def hello():
        typer.echo("hello from the dummy slice")

    root.add_typer(sub, name="dummy")


@check("dummy_content_is_dummy")
def dummy_content_is_dummy(ctx):
    """Content of every exchange is 'dummy'."""
    seen = [e.normalized.content for e in ctx.exchanges]
    return ok("all dummy") if seen and all(c == "dummy" for c in seen) else fail("not dummy", observed=seen)


@executor("dummy_twice")
async def dummy_twice(ctx):
    return [await ctx.client.send_canonical(ctx.request, stream=ctx.stream, label=f"turn{i}") for i in range(2)]


@register_defect
class DummyDefect(Defect):
    name = "dummy-defect"
    title = "replaces the answer"
    cases = ["DUMMY-001"]

    def on_reply(self, ctx, reply):
        reply.content = "not dummy"
        return reply


@register_page
class DummyPage(Page):
    id = "dummy"
    title = "Dummy"
    order = 99
    script = "dummy_page.js"

    def collect(self, runs):
        return {"count": len(runs), "kpis": [{"label": "Dummy runs", "value": len(runs)}]}
'''
PAGE_JS = (
    'GLMKit.registerPage("dummy", { render(panel, data, kit) {'
    ' panel.replaceChildren(kit.el("div", { class: "card card-b" }, "dummy tab: " + data.count)); } });\n'
)
CASE = {
    "id": "DUMMY-001",
    "title": "dummy executor + dummy check",
    "category": "dummy",
    "severity": "major",
    "executor": "dummy_twice",
    "modes": ["nonstream", "stream"],
    "source": {"urls": ["https://example.invalid/dummy"]},
    "request": {"messages": [{"role": "user", "content": "Reply dummy"}], "max_tokens": 64},
    "expect": {"default": [{"check": "http_status", "equals": 200}, {"check": "dummy_content_is_dummy"}]},
}


@pytest.fixture
def dummy_slice(tmp_path, monkeypatch) -> Iterator:
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "glmkit_dummy_plugin.py").write_text(textwrap.dedent(PLUGIN))
    (plug / "dummy_page.js").write_text(PAGE_JS)
    cases = tmp_path / "cases" / "dummyslice"
    cases.mkdir(parents=True)
    (cases / "DUMMY-001-twice.yaml").write_text(yaml.safe_dump(CASE))
    monkeypatch.syspath_prepend(str(plug))
    monkeypatch.setenv("GLMKIT_PLUGINS", "glmkit_dummy_plugin")
    monkeypatch.setenv("GLMKIT_CASES_DIRS", str(tmp_path / "cases"))
    yield tmp_path
    from glmkit.conform import checks, executors
    from glmkit.mock import defects
    from glmkit.report import registry

    checks._REGISTRY.pop("dummy_content_is_dummy", None)
    executors._REGISTRY.pop("dummy_twice", None)
    defects._REGISTRY.pop("dummy-defect", None)
    registry.unregister_page("dummy")
    sys.modules.pop("glmkit_dummy_plugin", None)


def test_dummy_slice_plugs_in_everywhere(dummy_slice) -> None:
    run = CliRunner()
    app = build_app()
    help_ = run.invoke(app, ["--help"]).output
    for cmd in ("mock", "conform", "report", "dummy"):
        assert cmd in help_
    assert run.invoke(app, ["dummy", "hello"]).output.strip() == "hello from the dummy slice"
    assert "dummy_content_is_dummy" in run.invoke(app, ["conform", "checks"]).output
    assert "dummy_twice" in run.invoke(app, ["conform", "executors"]).output
    assert "dummy-defect" in run.invoke(app, ["mock", "defects"]).output
    assert "DUMMY-001" in run.invoke(app, ["conform", "list"]).output

    results = dummy_slice / "results"
    clean = run.invoke(app, ["conform", "run", "-t", "mock", "-c", "DUMMY-*", "-o", str(results)])
    assert clean.exit_code == 0, clean.output
    assert "PASS  DUMMY-001[nonstream]" in clean.output and "PASS  DUMMY-001[stream]" in clean.output
    bad = run.invoke(app, ["conform", "run", "-t", "mock", "-c", "DUMMY-*", "-D", "dummy-defect", "-o", str(results)])
    assert bad.exit_code == 1 and "FAIL  DUMMY-001[nonstream]" in bad.output

    site = dummy_slice / "site"
    built = run.invoke(app, ["report", "build", str(results), "-o", str(site)])
    assert built.exit_code == 0, built.output
    assert (
        "dummy" in built.output.split("tabs=")[1].split()[0].split(",") and "token check: 0 finding(s)" in built.output
    )
    html = (site / "index.html").read_text()
    assert 'id="view-dummy"' in html and 'GLMKit.registerPage("dummy"' in html
    assert (site / "data" / "dummy.js").exists()
