"""Migration tab: built from an eval envelope, token-clean, hidden without eval runs."""

from __future__ import annotations

import json
from pathlib import Path

from glmkit.eval.demo import write_demo
from glmkit.eval.runner import EvalOptions, run_eval
from glmkit.report import tokencheck
from glmkit.report.builder import build_site


async def test_migration_tab_from_eval_run(tmp_path, monkeypatch) -> None:
    paths = write_demo(tmp_path / "demo")
    monkeypatch.setenv("GLMKIT_PROFILES_DIRS", str(paths["profiles"]))
    out = tmp_path / "results"
    await run_eval(
        EvalOptions(
            set_path=paths["set"],
            targets=["demo-glm-equal", "demo-glm-worse"],
            baseline="demo-incumbent",
            prices=[paths["prices"]],
            out_dir=out,
            seed=7,
            n_boot=300,
            task_types=["classify", "arithmetic"],
            concurrency=16,
        )
    )
    res = build_site(out, tmp_path / "site")
    assert "migration" in res.pages and res.findings == []
    data = json.loads((tmp_path / "site" / "data" / "migration.json").read_text())
    assert data["candidates"] == ["demo-glm-equal", "demo-glm-worse"] and data["baseline"] == "demo-incumbent"
    assert {c["task_type"] for c in data["comparisons"]} == {"classify", "arithmetic", "ALL"}
    item = data["items"][0]
    assert item["targets"]["demo-incumbent"]["replays"] and "prompt" in item


def test_tab_hidden_without_eval_runs(tmp_path) -> None:
    (tmp_path / "results").mkdir()
    assert "migration" not in build_site(tmp_path / "results", tmp_path / "site").pages


def test_token_check_fails_on_a_planted_raw_colour(tmp_path) -> None:
    css = Path(__file__).resolve().parent.parent / "src/glmkit/report/pages/migration.css"
    assert tokencheck.check_files([css]) == [] or all("raw-color" not in f for f in tokencheck.check_files([css]))
    bad = tmp_path / "bad.css"
    bad.write_text(css.read_text() + "\n.rec { color: #ff0000; }\n")
    assert any("raw-color" in f for f in tokencheck.check_files([bad]))
