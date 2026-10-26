"""Slice 03 end to end: every wire/anthropic/auth/limits case passes on the clean mock and fails on its planted
defect (the proof recorded in issues/03-conform-wire-anthropic.md)."""

from __future__ import annotations

import time

import pytest

from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.profile import get_profile
from glmkit.mock.defects import discover as defects
from glmkit.report.builder import build_site
from tests.test_report import _data

NEW = ["WIRE-00[3-9]", "WIRE-01*", "ANTH-*", "AUTH-*", "LIM-*"]


@pytest.mark.parametrize("profile", ["mock", "mock-anthropic", "mock-plan"])
async def test_new_cases_pass_on_clean_mock(profile) -> None:
    out = await run_conformance(get_profile(profile), RunOptions(patterns=NEW, include_opt_in=True, write=False))
    bad = [(i.id, i.reason) for i in out.envelope.items if i.status not in ("pass", "skip")]
    assert not bad
    assert out.envelope.summary.passed >= 19


# defect -> (profile, case glob, case ids that must fail)
MATRIX = {
    "anthropic-missing-max-tokens-500": ("mock-anthropic", "WIRE-008", {"WIRE-008"}),
    "tool-use-input-array-500": ("mock-anthropic", "WIRE-009", {"WIRE-009"}),
    "validation-500": ("mock", "WIRE-007", {"WIRE-007"}),
    "context-overflow-500": ("mock", "WIRE-018", {"WIRE-018"}),
    "auth-missing-500": ("mock-anthropic", "AUTH-001", {"AUTH-001"}),
    "silent-model-fallback": ("mock", "WIRE-006", {"WIRE-006"}),
    "alias-echo-model": ("mock-anthropic", "ANTH-005", {"ANTH-005"}),
    "zero-message-start-usage": ("mock-anthropic", "ANTH-001", {"ANTH-001"}),
    "inflated-token-counts": ("mock-anthropic", "ANTH-004", {"ANTH-004"}),
    "large-body-zero-usage": ("mock", "WIRE-010", {"WIRE-010"}),
    "stream-malformed": ("mock", "WIRE-015", {"WIRE-015"}),
    "reasoning-after-content": ("mock-anthropic", "WIRE-016", {"WIRE-016"}),
    "effort-ignored": ("mock", "WIRE-003", {"WIRE-003"}),
    "invalid-effort-accepted": ("mock", "WIRE-004", {"WIRE-004"}),
    "effort-none-still-thinks": ("mock", "WIRE-005", {"WIRE-005"}),
    "tool-choice-ignored": ("mock", "WIRE-011", {"WIRE-011"}),
    "stop-words-ignored": ("mock", "WIRE-012", {"WIRE-012"}),
    "json-mode-fenced": ("mock", "WIRE-014", {"WIRE-014"}),
    "do-sample-ignored": ("mock", "WIRE-013", {"WIRE-013"}),
    "anthropic-bearer-rejected": ("mock-anthropic", "ANTH-002", {"ANTH-002"}),
    "thinking-block-rejected": ("mock-anthropic", "ANTH-003", {"ANTH-003"}),
    "rate-limit-bare": ("mock", "LIM-001", {"LIM-001"}),
}


@pytest.mark.parametrize("defect", MATRIX)
async def test_case_fails_on_its_planted_defect(defect) -> None:
    profile, glob, ids = MATRIX[defect]
    clean = await run_conformance(get_profile(profile), RunOptions(patterns=[glob], write=False, include_opt_in=True))
    bad = await run_conformance(
        get_profile(profile), RunOptions(patterns=[glob], mock_defects=[defect], write=False, include_opt_in=True)
    )
    assert clean.envelope.verdict == "pass"
    failed = {i.group for i in bad.envelope.items if i.status == "fail"}
    assert failed == ids, [(i.id, i.status, i.reason) for i in bad.envelope.items]


async def test_hanging_stream_is_detected_within_the_timeout_and_never_hangs_the_runner() -> None:
    t0 = time.perf_counter()
    out = await run_conformance(
        get_profile("mock"),
        RunOptions(
            patterns=["WIRE-015", "WIRE-017"], mock_defects=["hanging-stream"], stream_timeout_s=1.5, write=False
        ),
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 12
    for item in out.envelope.items:
        assert item.status == "fail" and item.evidence[0].transport_error == "stream_timeout"
        assert item.evidence[0].normalized.stream.timed_out


async def test_rate_limit_burst_defect_is_throttled_not_failed() -> None:
    out = await run_conformance(
        get_profile("mock"), RunOptions(patterns=["LIM-001"], mock_defects=["rate-limit-burst"], write=False)
    )
    item = out.envelope.items[0]
    assert item.status == "throttled"
    assert next(c for c in item.checks if c.name == "wire_rate_limit_wellformed").status == "pass"


def test_every_new_defect_names_cases_that_exist() -> None:
    from glmkit.conform.cases import load_cases

    ids = {lc.case.id for lc in load_cases(include_opt_in=True)}
    for name, cls in defects().items():
        assert set(cls.cases) <= ids, name


async def test_errors_tab_groups_failures_by_taxonomy_category(tmp_path) -> None:
    out = tmp_path / "results"
    prof = get_profile("mock")
    await run_conformance(prof, RunOptions(patterns=["WIRE-007"], mock_defects=["validation-500"], out_dir=out))
    await run_conformance(prof, RunOptions(patterns=["WIRE-006"], mock_defects=["silent-model-fallback"], out_dir=out))
    anth = get_profile("mock-anthropic")
    await run_conformance(anth, RunOptions(patterns=["ANTH-004"], mock_defects=["inflated-token-counts"], out_dir=out))
    site = tmp_path / "site"
    res = build_site(out, site)
    assert "errors" in res.pages and res.findings == []
    data = _data(site, "errors")
    groups = {g["id"]: g for g in data["groups"]}
    assert groups["server"]["count"] == 5  # five WIRE-007 items answered HTTP 500 (no code)
    assert "contract" in groups  # WIRE-006 answered 200; ANTH-004 has no upstream error
    assert data["ratios"] and max(r["ratio"] for r in data["ratios"][0]["rows"]) == 2.8
    assert {r["code"] for r in data["taxonomy"]} >= {"1210", "1302", "1321"}
