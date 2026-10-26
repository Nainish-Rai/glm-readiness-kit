"""Evidence pack: citations, disclaimer, AI-BOM validation, residency gaps, dashboard tab."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.contracts.envelope import ItemResult, ResultEnvelope, new_run_id
from glmkit.evidence import aibom
from glmkit.evidence.checklist import CATALOGUE, CatalogueError, Citation, Ctx, Item, resolve_all, validate_catalogue
from glmkit.evidence.descriptor import Attestation, DescriptorError, load_deployment
from glmkit.evidence.disclaimer import DISCLAIMER
from glmkit.evidence.pack import build_pack, example_descriptors
from glmkit.evidence.render import MissingDisclaimer
from glmkit.report.builder import build_site
from glmkit.results import write_envelope

EXAMPLES = {p.stem: p for p in example_descriptors()}


def _fixture_results(out: Path) -> Path:
    """Fixture envelopes standing in for components built in parallel (conform, eval, guard, deploy)."""
    for comp, status in (("conform", "pass"), ("eval", "pass"), ("guard", "pass"), ("deploy", "pass")):
        env = ResultEnvelope(run_id=new_run_id(comp), kit_version="0.1.0", component=comp, started_at=datetime.now(UTC))
        env.items.append(ItemResult(id=f"{comp.upper()}-001", title="fixture", kind="fixture", status=status))
        write_envelope(env.finalize(), out)
    return out


@pytest.fixture
def results(tmp_path: Path) -> Path:
    return _fixture_results(tmp_path / "results")


def test_four_examples_exist() -> None:
    assert set(EXAMPLES) == {"self-hosted-eu", "mistral-regional", "bedrock", "zai-api"}
    assert {load_deployment(p).path for p in EXAMPLES.values()} == {
        "self-hosted",
        "mistral-regional",
        "bedrock",
        "zai-api",
    }


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_each_example_builds_a_pack(name: str, results: Path, tmp_path: Path) -> None:
    res = build_pack(EXAMPLES[name], results, tmp_path / "pack")
    assert res.bom_errors == [] and res.findings == []
    for f in (
        "index.html",
        "checklist.html",
        "data-flow.html",
        "license.html",
        "evidence-index.html",
        "ai-bom.html",
        "index.md",
        "dataflow.mmd",
        "dataflow.svg",
        "ai-bom.cdx.json",
        "manifest.json",
    ):
        assert (tmp_path / "pack" / f).exists(), f
    assert len(res.runs) == 4
    # evidence index links by run id
    assert all(r.run_id in (tmp_path / "pack" / "evidence-index.md").read_text() for r in res.runs)


@pytest.mark.parametrize("name", ["bedrock", "zai-api"])
def test_bedrock_and_zai_api_mark_eu_residency_gap(name: str, results: Path, tmp_path: Path) -> None:
    res = build_pack(EXAMPLES[name], results, tmp_path / "pack")
    r = next(i for i in res.items if i.id == "RES-1")
    assert r.status == "gap"
    assert ("no EU geo profile" in r.reason) if name == "bedrock" else ("no EU region found" in r.reason)


def test_residency_gap_cannot_be_attested_away(results: Path, tmp_path: Path) -> None:
    dep = load_deployment(EXAMPLES["bedrock"])
    dep.attestations["RES-1"] = Attestation(status="met", evidence_ref="contract.pdf")
    r = next(i for i in resolve_all(Ctx(dep=dep, runs=[], generated={})) if i.id == "RES-1")
    assert r.status == "gap"


def test_interpretive_item_is_never_met_without_counsel(results: Path) -> None:
    dep = load_deployment(EXAMPLES["self-hosted-eu"])
    dep.attestations["EU-A1"] = Attestation(status="met", evidence_ref="memo.pdf")
    r = next(i for i in resolve_all(Ctx(dep=dep, runs=[], generated={})) if i.id == "EU-A1")
    assert r.status == "needs-counsel"
    dep.attestations["EU-A1"] = Attestation(status="met", evidence_ref="memo.pdf", counsel_reviewed=True)
    r = next(i for i in resolve_all(Ctx(dep=dep, runs=[], generated={})) if i.id == "EU-A1")
    assert r.status == "met"


def test_met_always_has_evidence(results: Path, tmp_path: Path) -> None:
    for name in EXAMPLES:
        res = build_pack(EXAMPLES[name], results, tmp_path / name)
        assert all(i.evidence for i in res.items if i.status == "met")


def test_open_questions_stay_flagged() -> None:
    by_id = {i.id: i for i in CATALOGUE}
    assert by_id["EU-C5"].default == "needs-counsel"  # Art. 53(2) and the GLM-5.3 license
    assert any("UNVERIFIED" in f for f in by_id["EU-D1"].flags + by_id["EU-C3"].flags + by_id["EAR-1"].flags)


# ---- citations ---------------------------------------------------------------------------------------
def test_every_catalogue_item_has_a_valid_citation() -> None:
    validate_catalogue(CATALOGUE)
    assert all(i.citations and all(c.valid for c in i.citations) for i in CATALOGUE)


def test_item_without_citation_fails() -> None:
    planted = Item("EU-ZZ", "eu-ai-act", "planted", ("x",), ())
    with pytest.raises(CatalogueError, match="EU-ZZ: no citation"):
        validate_catalogue([*CATALOGUE, planted])
    undated = Item("EU-ZY", "eu-ai-act", "planted", ("x",), (Citation(label="a page", url="https://example.com"),))
    with pytest.raises(CatalogueError, match="EU-ZY"):
        validate_catalogue([undated])


def test_build_refuses_a_planted_item_without_citation(results: Path, tmp_path: Path) -> None:
    planted = Item("EU-ZZ", "eu-ai-act", "planted", ("x",), ())
    with pytest.raises(CatalogueError):
        build_pack(EXAMPLES["bedrock"], results, tmp_path / "p", catalogue=(*CATALOGUE, planted))


# ---- disclaimer --------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_disclaimer_on_every_generated_page(name: str, results: Path, tmp_path: Path) -> None:
    build_pack(EXAMPLES[name], results, tmp_path / "p", spdx=True)
    pages = [p for p in (tmp_path / "p").iterdir() if p.suffix in {".html", ".md", ".svg", ".mmd", ".json"}]
    assert len(pages) >= 15
    # JSON-escaped text of this disclaimer is identical (no quotes or backslashes in it)
    missing = [p.name for p in pages if DISCLAIMER not in p.read_text(encoding="utf-8")]
    assert missing == []


@pytest.mark.parametrize("fmt", ["html", "md"])
def test_template_without_disclaimer_fails_the_build(fmt: str, results: Path, tmp_path: Path) -> None:
    from glmkit.evidence.render import read_template

    stripped = read_template(f"page.{fmt}.tmpl").replace("{{DISCLAIMER}}", "")
    with pytest.raises(MissingDisclaimer):
        build_pack(EXAMPLES["bedrock"], results, tmp_path / "p", templates={fmt: stripped})


def test_shipped_templates_contain_the_placeholder() -> None:
    from glmkit.evidence.render import read_template

    assert "{{DISCLAIMER}}" in read_template("page.html.tmpl") and "{{DISCLAIMER}}" in read_template("page.md.tmpl")


# ---- AI-BOM ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_ai_bom_validates_against_1_6_and_1_7(name: str) -> None:
    doc = aibom.build_cyclonedx(load_deployment(EXAMPLES[name]), [], now=datetime(2026, 10, 26, tzinfo=UTC))
    assert doc["specVersion"] == "1.6" and aibom.validate_cyclonedx(doc) == []
    aibom.assert_valid(doc)
    model = doc["components"][0]
    assert model["type"] == "machine-learning-model" and "MIT-style" in model["licenses"][0]["license"]["name"]


def test_ai_bom_fails_on_planted_invalid_field() -> None:
    doc = aibom.build_cyclonedx(load_deployment(EXAMPLES["bedrock"]), [])
    bad = copy.deepcopy(doc)
    bad["components"][0]["type"] = "not-a-type"
    errs = aibom.validate_cyclonedx(bad)
    assert any("components[0].type" in e for e in errs)
    bad2 = copy.deepcopy(doc)
    bad2["bogus"] = 1
    with pytest.raises(aibom.BomValidationError):
        aibom.assert_valid(bad2)


def test_validate_bom_cli(tmp_path: Path) -> None:
    doc = aibom.build_cyclonedx(load_deployment(EXAMPLES["bedrock"]), [])
    ok, bad = tmp_path / "ok.json", tmp_path / "bad.json"
    ok.write_text(json.dumps(doc))
    doc["services"][0]["endpoints"] = "not-a-list"
    bad.write_text(json.dumps(doc))
    r = CliRunner()
    assert r.invoke(build_app(), ["evidence", "validate-bom", str(ok)]).exit_code == 0
    assert r.invoke(build_app(), ["evidence", "validate-bom", str(bad)]).exit_code == 1


def test_descriptor_path_rules(tmp_path: Path) -> None:
    f = tmp_path / "d.yaml"
    f.write_text("name: x\npath: bedrock\nhosting: {provider: AWS}\n")
    with pytest.raises(DescriptorError, match="bedrock_profile"):
        load_deployment(f)


# ---- CLI and dashboard ---------------------------------------------------------------------------------
def test_cli_build_and_dashboard_tab(results: Path, tmp_path: Path) -> None:
    r = CliRunner().invoke(
        build_app(),
        [
            "evidence",
            "build",
            "-d",
            str(EXAMPLES["bedrock"]),
            "-r",
            str(results),
            "-o",
            str(tmp_path / "pack"),
            "--spdx",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "token check: 0 finding(s)" in r.output and "not legal advice" in r.output
    site = tmp_path / "site"
    res = build_site(results, site)
    assert "evidence" in res.pages and res.findings == []
    data = json.loads((site / "data" / "evidence.json").read_text())
    dep = data["deployments"][0]
    assert dep["name"] == "acme-bedrock" and data["disclaimer"] == DISCLAIMER
    assert dep["items"][0]["status"] == "gap"  # gaps first
    assert next(i for i in dep["items"] if i["id"] == "RES-1")["status"] == "gap"
    assert len(dep["evidence_index"]) == 4
    assert {f["key"] for f in dep["frameworks"]} >= {"eu-ai-act", "gdpr", "singapore", "us-ear"}
