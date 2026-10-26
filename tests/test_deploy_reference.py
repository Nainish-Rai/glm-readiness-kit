"""Reference deployment files: lint, pinned versions, parsers pinned per engine, no secrets, health check."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from glmkit.cli import build_app

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
COMPOSE = {
    "vllm": DEPLOY / "vllm-single-node" / "docker-compose.yml",
    "sglang": DEPLOY / "sglang-single-node" / "docker-compose.yml",
}
VALUES = DEPLOY / "vllm-single-node" / "values.yaml"
LLMD = DEPLOY / "llm-d-cluster" / "profile.yaml"
PARSERS = {
    "vllm": {"--tool-call-parser": "glm47", "--reasoning-parser": "glm47"},
    "sglang": {"--tool-call-parser": "glm47", "--reasoning-parser": "glm45"},
    "llm-d": {"--tool-call-parser": "glm47", "--reasoning-parser": "glm45"},
}
PINNED_IMAGE = re.compile(r"^[\w./-]+:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$")


def _args(flags: list[str]) -> dict[str, str]:
    return dict(f.split("=", 1) for f in flags if "=" in f)


def _compose_flags(path: Path) -> list[str]:
    return yaml.safe_load(path.read_text())["services"][path.parent.name.split("-")[0]]["command"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
@pytest.mark.parametrize("engine", list(COMPOSE))
def test_compose_files_pass_docker_compose_config(engine) -> None:
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE[engine]), "config", "-q"], capture_output=True, text=True
    )
    if r.returncode != 0 and "unknown shorthand flag" in r.stderr + "docker: 'compose' is not":
        pytest.skip("docker compose plugin not available")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("engine", list(COMPOSE))
def test_compose_images_are_pinned_and_parsers_match_the_engine(engine) -> None:
    doc = yaml.safe_load(COMPOSE[engine].read_text())
    svc = next(iter(doc["services"].values()))
    assert PINNED_IMAGE.match(svc["image"]), svc["image"]
    flags = _args(_compose_flags(COMPOSE[engine]))
    for k, v in PARSERS[engine].items():
        assert flags[k] == v, f"{engine} {k}"


def test_vllm_single_node_flags_follow_the_recipe() -> None:
    flags = _compose_flags(COMPOSE["vllm"])
    assert "--enable-auto-tool-choice" in flags
    f = _args(flags)
    assert f["--kv-cache-dtype"] == "fp8" and f["--tensor-parallel-size"] == "8"
    assert f["--speculative-config.method"] == "mtp" and f["--model"] == "zai-org/GLM-5.3"
    assert int(f["--max-model-len"]) < 1_048_576  # never assume 1M on H200


def test_helm_values_match_schema_and_pin_the_engine() -> None:
    values = yaml.safe_load(VALUES.read_text())
    schema = json.loads((DEPLOY / "schemas" / "vllm-stack-values.schema.json").read_text())
    jsonschema.validate(values, schema)
    spec = values["servingEngineSpec"]["modelSpec"][0]
    assert spec["tag"] != "latest" and spec["imageDigest"]
    extra = _args(spec["vllmConfig"]["extraArgs"])
    for k, v in PARSERS["vllm"].items():
        assert extra[k] == v
    assert "--enable-auto-tool-choice" in spec["vllmConfig"]["extraArgs"]


def test_helm_schema_rejects_unpinned_or_unknown_values() -> None:
    values = yaml.safe_load(VALUES.read_text())
    schema = json.loads((DEPLOY / "schemas" / "vllm-stack-values.schema.json").read_text())
    values["servingEngineSpec"]["modelSpec"][0]["tag"] = "latest"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(values, schema)
    values = yaml.safe_load(VALUES.read_text())
    values["servingEngineSpec"]["modelSpec"][0]["typo"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(values, schema)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed (schema check above runs instead)")
def test_helm_lint() -> None:  # pragma: no cover - needs helm and the chart
    pytest.skip("helm lint needs the vllm-stack chart pulled; run it where helm and network are available")


def test_llm_d_profile_pins_release_engine_and_parsers() -> None:
    doc = yaml.safe_load(LLMD.read_text())
    assert re.fullmatch(r"v\d+\.\d+\.\d+", doc["llm_d"]["release"])
    assert PINNED_IMAGE.match(doc["engine"]["image"])
    f = _args(doc["flags"]["common"])
    for k, v in PARSERS["llm-d"].items():
        assert f[k] == v
    assert "--enable-auto-tool-choice" in doc["flags"]["common"]


def test_no_secrets_or_latest_tags_in_reference_files() -> None:
    for path in DEPLOY.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text()
        assert not re.search(r"hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9]{20,}", text), path
        assert not re.search(r":latest\b", text), path


def test_health_check_runs_wire_cases_against_the_mock(tmp_path) -> None:
    r = CliRunner().invoke(build_app(), ["deploy", "health", "--target", "mock", "--out", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "WIRE-001" in r.output and "verdict: pass" in r.output


def test_health_check_fails_against_a_planted_defect(tmp_path) -> None:
    r = CliRunner().invoke(
        build_app(),
        ["deploy", "health", "--target", "mock", "--mock-defect", "forced-thinking-500", "--out", str(tmp_path)],
    )
    assert r.exit_code == 1 and "verdict: fail" in r.output


def test_health_check_reports_an_unreachable_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLMKIT_VLLM_BASE_URL", "http://127.0.0.1:9/v1")
    r = CliRunner().invoke(build_app(), ["deploy", "health", "--target", "vllm-local", "--out", str(tmp_path)])
    assert r.exit_code == 1 and "NOT READY" in r.output
