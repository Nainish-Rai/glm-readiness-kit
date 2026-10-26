"""Engagement workspace: init, derived state, the one-command demo on the mock, the Engagement tab."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.report.builder import build_site
from glmkit.results import load_envelopes
from glmkit.workbench.workspace import derive_state, find_workspace, init_workspace, read_brief


def test_init_scaffolds_the_workspace(tmp_path: Path) -> None:
    ws = init_workspace("Acme Corp", tmp_path)
    assert ws.name == "acme-corp"
    for rel in (
        "engagement.yaml",
        "brief.md",
        "README.md",
        "Makefile",
        "golden/golden-set.template.yaml",
        "profiles/incumbent.yaml.example",
    ):
        assert (ws / rel).is_file(), rel
    readme = (ws / "README.md").read_text()
    assert "Week 1" in readme and "90-day" in readme
    brief = (ws / "brief.md").read_text()
    assert all(h in brief for h in ("Use cases", "Success metrics", "Constraints", "Data residency"))
    with pytest.raises(ValueError):
        init_workspace("Acme Corp", tmp_path)


def test_makefile_targets_call_real_commands(tmp_path: Path) -> None:
    ws = init_workspace("acme", tmp_path)
    if shutil.which("make") is None:
        pytest.skip("make not installed")
    out = subprocess.run(["make", "-n", "all"], cwd=ws, capture_output=True, text=True, check=True).stdout
    for cmd in (
        "glmkit conform run",
        "glmkit eval run",
        "glmkit cost analyze",
        "glmkit evidence build",
        "glmkit report build",
    ):
        assert cmd in out
    app = build_app()
    names = {c.name for c in app.registered_commands} | {g.name for g in app.registered_groups}
    assert {"init", "status", "demo", "conform", "eval", "cost", "evidence", "report"} <= names


def test_state_is_derived_from_files_not_flags(tmp_path: Path) -> None:
    ws = init_workspace("acme", tmp_path)
    st = derive_state(ws, [])
    assert st.done == 0 and st.brief_todos > 0
    (ws / "brief.md").write_text("# b\n## Use cases\nTriage tickets.\n## Success metrics\n5 points.\n")
    (ws / "profiles" / "incumbent.yaml").write_text("name: incumbent\n")
    st = derive_state(ws, [])
    assert {s.id for s in st.steps if s.done} == {"brief", "profiles"}
    assert read_brief(ws / "brief.md")[1] == 0
    assert find_workspace(ws / "profiles") == ws.resolve()


@pytest.fixture(scope="module")
def demo_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("eng")
    ws = init_workspace("acme", base)
    import os
    import sys

    env = {**os.environ, "GLMKIT_PLUGINS": ""}
    r = subprocess.run(
        [sys.executable, "-m", "glmkit", "demo", "run", "banking77", "--max-items", "30", "--skip", "conform"],
        cwd=ws, capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    ws_out = ws / "demo-run.txt"
    ws_out.write_text(r.stdout)
    return ws


def test_demo_run_says_which_mode_ran_and_builds_the_dashboard(demo_workspace: Path) -> None:
    out = (demo_workspace / "demo-run.txt").read_text()
    assert (
        "data:    banking77=stand-in" in out
        and "MOCK server" in out
        and "mode that ran: data stand-in; targets mock" in out
    )
    assert (demo_workspace / "site" / "index.html").is_file()
    envs = [e for _, e in load_envelopes(demo_workspace / "results")[0]]
    assert {e.component for e in envs} >= {"eval", "cost", "evidence"}
    st = derive_state(demo_workspace, envs)
    done = {s.id for s in st.steps if s.done}
    assert {"eval", "cost", "evidence", "report", "golden"} <= done and "conform" not in done and "brief" not in done


def test_engagement_tab_data_and_token_check(
    demo_workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GLMKIT_WORKSPACE", str(demo_workspace))
    built = build_site(demo_workspace / "results", tmp_path / "site")
    assert "engagement" in built.pages and built.pages[0] == "engagement" and built.findings == []
    data = json.loads((tmp_path / "site" / "data" / "engagement.json").read_text())
    assert data["customer"] == "acme" and data["last_run"]["targets"] == "mock"
    assert any(s["id"] == "eval" and s["done"] and s["run_id"] for s in data["steps"])
    assert [b["title"] for b in data["brief"]][:2] == ["Use cases", "Success metrics"]


def test_engagement_tab_hidden_outside_a_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLMKIT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results").mkdir()
    assert "engagement" not in build_site(tmp_path / "results", tmp_path / "site").pages


def test_token_check_fails_on_a_planted_raw_colour_in_the_engagement_css(
    demo_workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from glmkit.report.builder import check_site

    monkeypatch.setenv("GLMKIT_WORKSPACE", str(demo_workspace))
    site = tmp_path / "site"
    assert build_site(demo_workspace / "results", site).findings == []
    index = site / "index.html"
    index.write_text(
        index.read_text().replace(
            "/* Engagement tab. Tokens only. */",
            "/* Engagement tab. Tokens only. */\n.eg-brief h4 { color: #ff0000; }",
            1,
        )
    )
    assert check_site(site)


def test_status_command(demo_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(demo_workspace)
    r = CliRunner().invoke(build_app(), ["status"])
    assert r.exit_code == 0 and "[x] Migration eval" in r.output and "[ ] Engagement brief" in r.output
    monkeypatch.chdir(demo_workspace.parent)
    assert CliRunner().invoke(build_app(), ["status"]).exit_code == 2
