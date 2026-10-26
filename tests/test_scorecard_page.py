"""Scorecard tab data: provider x category rates with Wilson CIs, hard gates, schema-shape sensitivity."""

from __future__ import annotations

from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.profile import get_profile
from glmkit.report.pages.scorecard import ScorecardPage
from glmkit.report.registry import RunRef


async def ref(defects, path):
    out = await run_conformance(
        get_profile("mock"), RunOptions(patterns=["TOOL-001", "TOOL-006"], mock_defects=defects, write=False)
    )
    return RunRef(envelope=out.envelope, path=path)


async def test_scorecard_clean_vs_defective_provider() -> None:
    data = ScorecardPage().collect([await ref([], "a"), await ref(["oneof-empty-args", "flaky-tool-call"], "b")])
    clean, bad = data["providers"]
    assert clean["verdict"] == "pass" and clean["hard_failures"] == 0
    assert bad["verdict"] == "fail" and bad["hard_failures"] >= 2
    cell = bad["cells"]["tools/basic"]
    assert cell["trials"] == 40 and cell["ci_low"] < cell["rate"] < cell["ci_high"] <= 1
    assert data["hard_gates"] and all(h["ref"] in data["items"] for h in data["hard_gates"])
    sens = {
        s["shape"]: s["sensitivity"] for s in next(s for s in data["shapes"] if s["provider"] == bad["key"])["shapes"]
    }
    assert sens["flat"] is None and "oneof" in sens
    assert data["count"] == 2 and data["kpis"][1]["value"] == len(data["hard_gates"])


def test_scorecard_hidden_without_conformance_runs() -> None:
    assert ScorecardPage().collect([]) is None
