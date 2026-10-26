"""Shared contracts: committed JSON Schemas match the models; cases, envelopes and traces validate."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import yaml

from glmkit.conform.cases import CaseError, load_case, load_cases
from glmkit.contracts.envelope import ItemResult, ResultEnvelope, compute_verdict
from glmkit.contracts.schemas import SCHEMAS, render, validate
from tests.conftest import REPO


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_committed_schema_matches_model(name: str) -> None:
    committed = (REPO / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8")
    assert committed == render(name), f"schemas/{name}.schema.json is stale: run `glmkit schema export`"


def test_shipped_cases_validate_against_exported_schema() -> None:
    cases = load_cases()
    assert {"WIRE-001", "WIRE-002"} <= {lc.case.id for lc in cases}
    for lc in cases:
        raw = yaml.safe_load(lc.path.read_text(encoding="utf-8"))
        validate("conformance-case", json.loads(json.dumps(raw, default=str)))  # YAML dates -> ISO strings
        assert lc.case.source.issues or lc.case.source.urls, f"{lc.case.id} has no provenance"


def test_case_file_name_must_start_with_id(tmp_path) -> None:
    d = tmp_path / "wire"
    d.mkdir()
    src = (REPO / "cases/wire/WIRE-002-thinking-enabled-low.yaml").read_text()
    (d / "WRONG-name.yaml").write_text(src)
    with pytest.raises(CaseError, match="must start with the case id"):
        load_case(d / "WRONG-name.yaml", tmp_path)


def test_case_ref_resolves_fixture_and_cannot_escape_root(tmp_path) -> None:
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "msgs.json").write_text(json.dumps([{"role": "user", "content": "hi"}]))
    (tmp_path / "x").mkdir()
    case = {
        "id": "TEST-001",
        "title": "t",
        "category": "x",
        "severity": "info",
        "request": {"messages": {"$ref": "fixtures/msgs.json"}},
        "expect": {"default": [{"check": "no_5xx"}]},
    }
    (tmp_path / "x" / "TEST-001-ref.yaml").write_text(yaml.safe_dump(case))
    lc = load_case(tmp_path / "x" / "TEST-001-ref.yaml", tmp_path)
    assert lc.case.request.messages == [{"role": "user", "content": "hi"}]
    case["request"]["messages"] = {"$ref": "../../etc/passwd"}
    (tmp_path / "x" / "TEST-001-ref.yaml").write_text(yaml.safe_dump(case))
    with pytest.raises(CaseError, match="escapes"):
        load_case(tmp_path / "x" / "TEST-001-ref.yaml", tmp_path)


def test_unknown_expect_class_rejected(tmp_path) -> None:
    (tmp_path / "x").mkdir()
    case = {"id": "TEST-002", "title": "t", "category": "x", "severity": "info", "expect": {"apii": []}}
    (tmp_path / "x" / "TEST-002.yaml").write_text(yaml.safe_dump(case))
    with pytest.raises(CaseError, match="unknown expect keys"):
        load_case(tmp_path / "x" / "TEST-002.yaml", tmp_path)


def _item(status: str, severity: str | None = "critical") -> ItemResult:
    return ItemResult(id=status, status=status, severity=severity)


@pytest.mark.parametrize(
    ("statuses", "verdict"),
    [
        ([], "incomplete"),
        ([("skip", "critical")], "incomplete"),
        ([("pass", "critical")], "pass"),
        ([("pass", "critical"), ("fail", "critical")], "fail"),
        ([("pass", "critical"), ("fail", "minor")], "warn"),
        ([("pass", "critical"), ("soft_fail", "critical")], "warn"),
        ([("pass", "major"), ("fail", "major")], "fail"),
    ],
)
def test_verdict_rules(statuses, verdict) -> None:
    assert compute_verdict([_item(s, sev) for s, sev in statuses]) == verdict


def test_envelope_round_trips_schema() -> None:
    env = ResultEnvelope(run_id="r1", component="test", started_at=datetime.now(UTC), items=[_item("pass")]).finalize()
    validate("result-envelope", env.model_dump(mode="json"))
    assert env.summary.pass_rate == 1.0
