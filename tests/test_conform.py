"""Conformance runner end to end against the mock (WIRE-001/002 with and without the planted defect)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.profile import get_profile
from glmkit.contracts.schemas import validate
from glmkit.mock import MockConfig, MockServer
from tests.conftest import profile_at

runner = CliRunner()


def statuses(env) -> dict[str, str]:
    return {i.id: i.status for i in env.items}


async def test_wire_cases_pass_on_clean_mock_both_protocols(tmp_path) -> None:
    for name in ("mock", "mock-anthropic"):
        out = await run_conformance(get_profile(name), RunOptions(patterns=["WIRE-001", "WIRE-002"], out_dir=tmp_path))
        assert statuses(out.envelope) == {
            "WIRE-001[nonstream]": "pass",
            "WIRE-002[nonstream]": "pass",
            "WIRE-002[stream]": "pass",
        }
        assert out.envelope.verdict == "pass"
        data = json.loads((out.run_dir / "result.json").read_text())
        validate("result-envelope", data)
        for line in (out.run_dir / "traces.jsonl").read_text().splitlines():
            validate("switch-trace-v0", json.loads(line))


async def test_wire_001_fails_on_planted_defect_with_traceback_evidence(tmp_path) -> None:
    out = await run_conformance(
        get_profile("mock"),
        RunOptions(patterns=["WIRE-001", "WIRE-002"], mock_defects=["forced-thinking-500"], out_dir=tmp_path),
    )
    env = out.envelope
    assert statuses(env)["WIRE-001[nonstream]"] == "fail" and statuses(env)["WIRE-002[stream]"] == "pass"
    assert env.verdict == "fail" and env.summary.hard_gate_failures == 1
    item = next(i for i in env.items if i.id == "WIRE-001[nonstream]")
    assert {c.name for c in item.checks if c.status == "fail"} >= {"no_5xx", "no_internal_leak", "http_status"}
    assert item.evidence[0].response.status == 500 and "Traceback" in item.evidence[0].response.body_text


async def test_per_request_defect_header_against_external_mock(tmp_path) -> None:
    with MockServer(MockConfig()) as srv:
        profile = get_profile("mock-server").with_base_url(srv.url + "/api/paas/v4")
        clean = await run_conformance(profile, RunOptions(patterns=["WIRE-001"], out_dir=tmp_path))
        bad = await run_conformance(
            profile, RunOptions(patterns=["WIRE-001"], mock_defects=["forced-thinking-500"], out_dir=tmp_path)
        )
    assert clean.envelope.verdict == "pass" and bad.envelope.verdict == "fail"
    assert bad.envelope.items[0].evidence[0].request.headers["x-glmkit-defects"] == "forced-thinking-500"


async def test_plan_class_soft_expectations(tmp_path) -> None:
    out = await run_conformance(get_profile("mock-plan"), RunOptions(patterns=["WIRE-001"], write=False))
    item = out.envelope.items[0]
    assert item.status == "pass" and item.metrics["http_status"] == 200
    assert next(c for c in item.checks if c.name == "error_code").status == "skip"  # `when` precondition


async def test_applicability_and_mode_filter(mock_server, tmp_path) -> None:
    p = profile_at("mock", mock_server).model_copy(update={"model": "glm-5.2"})
    out = await run_conformance(p, RunOptions(patterns=["WIRE-001", "WIRE-002"], write=False))
    assert {i.status for i in out.envelope.items} == {"skip"} and out.envelope.verdict == "incomplete"
    out = await run_conformance(get_profile("mock"), RunOptions(patterns=["WIRE-002"], modes=["stream"], write=False))
    assert statuses(out.envelope) == {"WIRE-002[stream]": "pass"}


def _write_case(root: Path, case: dict) -> None:
    (root / "x").mkdir(parents=True, exist_ok=True)
    (root / "x" / f"{case['id']}.yaml").write_text(yaml.safe_dump(case))


async def test_repeats_aggregate_and_unknown_check(tmp_path) -> None:
    base = {
        "title": "t",
        "category": "x",
        "severity": "major",
        "request": {"messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 64},
    }
    _write_case(
        tmp_path,
        {
            **base,
            "id": "TEST-001",
            "kind": "statistical",
            "repeats": 3,
            "expect": {
                "default": [{"check": "content_present", "matches": "^OK$"}, {"check": "pass_rate", "gte": 1.0}]
            },
        },
    )
    _write_case(tmp_path, {**base, "id": "TEST-002", "expect": {"default": [{"check": "no_such_check"}]}})
    out = await run_conformance(get_profile("mock"), RunOptions(case_roots=[tmp_path], write=False))
    items = {i.group: i for i in out.envelope.items}
    assert items["TEST-001"].status == "pass" and items["TEST-001"].metrics["attempts"] == 3
    assert items["TEST-001"].metrics["pass_rate"] == 1.0
    assert items["TEST-002"].status == "error" and "unknown check" in items["TEST-002"].reason


def test_cli_exit_codes_and_output(tmp_path) -> None:
    app = build_app()
    ok = runner.invoke(app, ["conform", "run", "--target", "mock", "--cases", "WIRE-00*", "--out", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    assert "verdict: pass" in ok.output
    bad = runner.invoke(
        app, ["conform", "run", "-t", "mock", "-c", "WIRE-00*", "-D", "forced-thinking-500", "-o", str(tmp_path)]
    )
    assert bad.exit_code == 1 and "FAIL  WIRE-001[nonstream]" in bad.output
    unknown = runner.invoke(app, ["conform", "run", "--target", "nope"])
    assert unknown.exit_code == 2
