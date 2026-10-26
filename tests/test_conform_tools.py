"""TOOL-001 ... TOOL-015 end to end against the mock: clean passes, every planted defect is caught."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.profile import get_profile

runner = CliRunner()


async def run(cases, *, target="mock", defects=(), tmp_path=None, opt_in=True):
    out = await run_conformance(
        get_profile(target),
        RunOptions(
            patterns=list(cases), mock_defects=list(defects), include_opt_in=opt_in, write=False, out_dir=tmp_path
        ),
    )
    return out.envelope


@pytest.mark.parametrize("target", ["mock", "mock-anthropic", "mock-plan"])
async def test_all_tool_cases_pass_on_clean_mock(target, tmp_path) -> None:
    env = await run(["TOOL-*"], target=target, tmp_path=tmp_path)
    assert {i.group for i in env.items} == {f"TOOL-{n:03d}" for n in range(1, 16)}
    assert [i.id for i in env.items if i.status != "pass"] == []
    assert env.verdict == "pass"


# defect -> case groups that must fail (and the groups that must keep passing)
DEFECTS = [
    ("flaky-tool-call", "TOOL-001"),
    ("tools-always-called", "TOOL-002"),
    ("duplicate-tool-ids", "TOOL-003"),
    ("duplicate-tool-ids", "TOOL-012"),
    ("typed-args-stringified", "TOOL-004"),
    ("tool-call-end-truncation", "TOOL-005"),
    ("oneof-empty-args", "TOOL-006"),
    ("many-tools-broken", "TOOL-007"),
    ("unknown-tool-name", "TOOL-008"),
    ("dict-only-chat-template", "TOOL-009"),
    ("null-content-rejected", "TOOL-009"),
    ("empty-tool-result-crash", "TOOL-010"),
    ("tool-marker-leak", "TOOL-011"),
    ("long-context-tool-drop", "TOOL-013"),
    ("command-prefix-dropped", "TOOL-014"),
    ("tools-always-called", "TOOL-015"),
]


@pytest.mark.parametrize(("defect", "case"), DEFECTS)
async def test_planted_defect_fails_its_case(defect, case, tmp_path) -> None:
    env = await run([case], defects=[defect], tmp_path=tmp_path)
    failed = [i for i in env.items if i.status == "fail"]
    assert failed, f"{defect} was not caught by {case}"
    assert env.verdict in ("fail", "warn")


async def test_oneof_defect_only_breaks_the_oneof_shape(tmp_path) -> None:
    env = await run(["TOOL-006"], defects=["oneof-empty-args"], tmp_path=tmp_path)
    by = {i.id: i.status for i in env.items}
    assert by["TOOL-006[nonstream]"] == "fail" and by["TOOL-006/flat[nonstream]"] == "pass"
    assert by["TOOL-006/anyof[stream]"] == "pass" and by["TOOL-006/refs[stream]"] == "pass"
    item = next(i for i in env.items if i.id == "TOOL-006[nonstream]")
    assert "SchemaMismatch" in item.reason and item.evidence[0].normalized.tool_calls[0].arguments == "{}"


async def test_statistical_case_reports_wilson_ci(tmp_path) -> None:
    env = await run(["TOOL-001"], tmp_path=tmp_path)
    item = env.items[0]
    agg = next(c for c in item.checks if c.name == "pass_rate_wilson")
    assert item.metrics["attempts"] == 20 and item.metrics["pass_rate"] == 1.0
    assert (
        agg.observed["n"] == 20
        and agg.observed["ci_low"] == pytest.approx(0.839, abs=1e-3)
        and agg.observed["ci_high"] == 1.0
    )
    bad = (await run(["TOOL-001"], defects=["flaky-tool-call"], tmp_path=tmp_path)).items[0]
    obs = next(c for c in bad.checks if c.name == "pass_rate_wilson").observed
    assert (obs["passes"], obs["n"]) == (15, 20) and bad.status == "fail"


async def test_critical_statistical_failure_cannot_be_averaged_away(tmp_path) -> None:
    env = await run(["TOOL-001", "TOOL-002", "TOOL-009", "TOOL-010"], defects=["flaky-tool-call"], tmp_path=tmp_path)
    passed = [i for i in env.items if i.status == "pass"]
    assert len(passed) == 9  # every other item passes, and still:
    assert env.summary.hard_gate_failures >= 1 and env.verdict == "fail"
    assert [i.group for i in env.items if i.status == "fail"] == ["TOOL-001", "TOOL-001"]


async def test_opt_in_soak_case_only_runs_when_named(tmp_path) -> None:
    env = await run(["TOOL-01*"], tmp_path=tmp_path, opt_in=False)
    assert "TOOL-014" not in {i.group for i in env.items}
    env = await run(["TOOL-014"], tmp_path=tmp_path, opt_in=False)
    assert [i.group for i in env.items] == ["TOOL-014"]


async def test_large_context_evidence_is_compacted(tmp_path) -> None:
    env = await run(["TOOL-013"], tmp_path=tmp_path)
    ex = env.items[0].evidence[0]
    assert ex.request.body_bytes > 200_000
    assert len(json.dumps(ex.request.body)) < 20_000


def test_k2vv_command_reports_f1_and_schema_accuracy(tmp_path) -> None:
    res = runner.invoke(
        build_app(),
        ["conform", "k2vv", "corpora/k2vv-sample.jsonl", "--target", "mock", "--out", str(tmp_path), "--json"],
    )
    assert res.exit_code == 0, res.output
    scores = json.loads(res.output.split("\n{", 1)[0] if res.output.startswith("{") is False else res.output)
    assert scores["n"] == 20 and scores["f1"] == pytest.approx(1.0, abs=1e-3) and scores["schema_accuracy"] == 1.0
    bad = runner.invoke(
        build_app(),
        [
            "conform",
            "k2vv",
            "corpora/k2vv-sample.jsonl",
            "--target",
            "mock",
            "-D",
            "tools-always-called",
            "--out",
            str(tmp_path),
            "--json",
        ],
    )
    assert json.loads(bad.output)["f1"] < 0.73


def test_k2vv_command_rejects_corpus_without_reference(tmp_path) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n")
    res = runner.invoke(build_app(), ["conform", "k2vv", str(corpus), "--target", "mock", "--out", str(tmp_path)])
    assert res.exit_code == 2
