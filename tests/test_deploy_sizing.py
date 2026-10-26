"""Sizing calculator: published figures, infeasible configs, sources, MoE terms, Python/JS parity."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.deploy import sizing

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src" / "glmkit" / "report" / "pages" / "deploy.js"
runner = CliRunner()


@pytest.fixture(scope="module")
def payload():
    return sizing.build_payload()


# ---- published figures ----------------------------------------------------------------------------------------


def test_kv_per_token_reproduces_llmd_44_9_kb(payload) -> None:
    r = sizing.size({}, payload)
    kv = r["kv"]["bytes_per_token_mla"]
    assert kv == 78 * (512 + 64)
    assert abs(kv - 44_900) / 44_900 <= 0.01


def test_fp8_weights_reproduce_755_7_gb(payload) -> None:
    assert payload["spec"]["checkpoint_gb"]["fp8"] == pytest.approx(755.7, rel=0.001)
    assert sizing.size({}, payload)["weights"]["total_gb"] == pytest.approx(755.7, rel=0.001)


def test_tp8_h200_kv_capacity_matches_llmd_598k(payload) -> None:
    r = sizing.size({"context": 131072}, payload)
    assert r["kv"]["max_tokens"] == pytest.approx(598_000, rel=0.01)


def test_validate_passes_and_fails_when_a_config_value_is_perturbed() -> None:
    assert all(v["ok"] for v in sizing.validate())
    bad = sizing.load_config()
    bad["config"]["kv_lora_rank"] = 1024
    kv = next(v for v in sizing.validate(bad) if v["name"] == "kv_bytes_per_token_fp8")
    assert not kv["ok"] and kv["computed"] == 78 * (1024 + 64)
    mf = sizing.load_manifest()
    mf["files"] = [
        f for f in mf["files"] if not f[0].endswith("00010-of-00142.safetensors") and f[0] != mf["files"][40][0]
    ]
    w = next(v for v in sizing.validate(None, mf) if v["name"] == "fp8_checkpoint_gb")
    assert not w["ok"]


def test_research_cost_worked_examples() -> None:
    ref = sizing.load_catalog()["reference_costs"]
    a = ref["nebius_h200_sglang_c100"]
    assert sizing.cost_per_m_from_throughput(a["gpu_usd_hr"], a["n_gpus"], a["total_tok_s"]) == pytest.approx(
        a["usd_m_blended"], abs=0.01
    )
    b = ref["llmd_discounted_same_throughput"]
    assert sizing.cost_per_m_from_throughput(b["gpu_usd_hr"], b["n_gpus"], b["total_tok_s"]) == pytest.approx(
        b["usd_m_blended"], abs=0.01
    )
    c = ref["llmd_64xh200_output_only"]
    assert sizing.cost_per_m_from_throughput(c["gpu_usd_hr"], c["n_gpus"], c["out_tok_s"]) == pytest.approx(
        c["usd_m_out"], abs=0.01
    )


# ---- feasibility ----------------------------------------------------------------------------------------------


def test_one_million_context_on_h200_tp8_is_infeasible_with_reason(payload) -> None:
    r = sizing.size({"context": sizing.parse_tokens("1m")}, payload)
    f = r["feasibility"]
    assert not f["feasible"] and f["weights_fit"] and not f["context_fits"]
    assert "replicated" in f["reasons"][0] and "context 1,048,576" in f["reasons"][0]


def test_one_million_context_fits_on_8_b200(payload) -> None:
    assert sizing.size({"gpu": "b200", "context": sizing.parse_tokens("1m"), "concurrency": 8}, payload)["feasibility"][
        "feasible"
    ]


def test_weights_do_not_fit_on_two_h200(payload) -> None:
    r = sizing.size({"n_gpus": 2}, payload)
    assert not r["feasibility"]["weights_fit"] and "weights" in r["feasibility"]["reasons"][0]
    assert r["perf"] is None and r["cost"] is None


def test_tp_does_not_multiply_kv_but_dcp_does(payload) -> None:
    """KV capacity is free-bytes-per-rank / bytes-per-token: the cache is one copy per rank, not N x."""
    for n in (8, 16):
        r = sizing.size({"n_gpus": n}, payload)
        assert r["kv"]["max_tokens"] == pytest.approx(r["memory"]["kv_free_per_gpu_gb"] * 1e9 / 44_928)
    base = sizing.size({}, payload)["kv"]["max_tokens"]
    assert sizing.size({"dcp": 8}, payload)["kv"]["max_tokens"] == pytest.approx(8 * base)


def test_concurrency_shortfall_is_soft(payload) -> None:
    r = sizing.size({"concurrency": 400}, payload)
    f = r["feasibility"]
    assert f["feasible"] and not f["concurrency_fits"] and "max concurrency" in f["reasons"][0]
    assert r["moe"]["batch"] == r["kv"]["max_concurrency"]


def test_flash_is_refused() -> None:
    with pytest.raises(sizing.SizingError, match="not a plain MLA"):
        sizing.size({"model": "glm-5.3-flash"})


def test_parse_tokens() -> None:
    assert sizing.parse_tokens("128k") == 131072 and sizing.parse_tokens("1m") == 1_048_576
    with pytest.raises(sizing.SizingError):
        sizing.parse_tokens("lots")


# ---- MoE terms and calibration --------------------------------------------------------------------------------


@pytest.mark.parametrize("batch,expected", [(1, 8), (4, 31), (16, 102), (32, 163), (64, 222), (128, 252)])
def test_experts_touched_matches_research_table(batch, expected) -> None:
    assert sizing.experts_touched(256, 8, batch) == pytest.approx(expected, abs=1)


def test_fit_reproduces_stored_constants_and_calibration_points(payload) -> None:
    cat = sizing.load_catalog()
    fit = sizing.fit_calibration(payload, cat["calibration"])
    assert fit["eta"] == pytest.approx(cat["fitted"]["eta"], rel=0.01)
    assert fit["routing_skew"] == pytest.approx(cat["fitted"]["routing_skew"], rel=0.01)
    for key in ("sglang_b1", "sglang_c100"):
        p = cat["calibration"][key]
        r = sizing.size(
            {
                "gpu": p["gpu"],
                "n_gpus": p["n_gpus"],
                "concurrency": p["batch"],
                "avg_input": 1000,
                "avg_output": 1000,
                "context": 4096,
            },
            payload,
        )
        assert r["perf"]["decode_tok_s"] == pytest.approx(p["out_tok_s"], rel=0.02)


def test_active_params_speed_claim_only_holds_at_low_batch(payload) -> None:
    low = sizing.size({"concurrency": 1}, payload)["moe"]["read_fraction_of_weights"]
    high = sizing.size({"concurrency": 64, "routing_skew": 0.0}, payload)["moe"]["read_fraction_of_weights"]
    assert low < 0.06 and high > 0.85


def test_mtp_and_effort_move_the_right_way(payload) -> None:
    base = sizing.size({}, payload)
    mtp = sizing.size({"mtp_accept": 2.9}, payload)
    assert mtp["perf"]["decode_tok_s"] == pytest.approx(2.9 * base["perf"]["decode_tok_s"])
    hi = sizing.size({"effort_multiplier": 3.0}, payload)
    assert hi["cost"]["api_usd_per_request"] > base["cost"]["api_usd_per_request"]


def test_breakeven_utilisation_is_where_self_cost_meets_api(payload) -> None:
    r = sizing.size({"gpu": "h200-discounted", "mtp_accept": 2.9, "prefix_reuse": 0.9}, payload)
    c = r["cost"]
    assert c["breakeven_reachable"]
    at = sizing.size(
        {"gpu": "h200-discounted", "mtp_accept": 2.9, "prefix_reuse": 0.9, "utilisation": c["breakeven_utilisation"]},
        payload,
    )["cost"]
    assert at["self_usd_m"] == pytest.approx(at["api_usd_m"])


# ---- sources enforced -----------------------------------------------------------------------------------------


def _has_source(src: dict) -> bool:
    return bool(src.get("url", "").startswith("https://")) and bool(
        __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}", src.get("date", ""))
    )


def test_every_price_and_calibration_point_has_source_url_and_date() -> None:
    cat = sizing.load_catalog()
    for k, g in cat["gpus"].items():
        assert _has_source(g["price_source"]), f"gpu price {k}"
        assert _has_source(g["spec_source"]), f"gpu spec {k}"
    for k, a in cat["apis"].items():
        assert _has_source(a["source"]), f"api {k}"
    for k, c in cat["calibration"].items():
        assert _has_source(c), f"calibration {k}"
    for k in ("bf16", "nvfp4", "fp8"):
        assert _has_source(cat["checkpoints"][k]), f"checkpoint {k}"
    assert _has_source(cat["model"]["active_params_source"])


def test_source_check_would_fail_without_a_date() -> None:
    cat = copy.deepcopy(sizing.load_catalog())
    del cat["gpus"]["h200"]["price_source"]["date"]
    assert not _has_source(cat["gpus"]["h200"]["price_source"])


# ---- Python and JS give equal outputs -------------------------------------------------------------------------

CASES = [
    {},
    {"gpu": "b200", "context": 1_048_576, "concurrency": 8},
    {"context": 1_048_576},
    {"n_gpus": 2},
    {"precision": "bf16", "gpu": "b300", "n_gpus": 8, "concurrency": 32},
    {"precision": "nvfp4", "gpu": "b200", "n_gpus": 4, "kv_dtype": "bf16", "include_indexer": True},
    {"prefix_reuse": 0.9, "mtp_accept": 2.95, "effort_multiplier": 2.5, "api": "bedrock-global", "utilisation": 0.8},
    {"concurrency": 1, "avg_input": 1000, "avg_output": 1000, "context": 4096, "api": "mistral"},
    {"dcp": 4, "n_gpus": 16, "gpu": "h100", "routing_skew": 0.2, "eta": 0.2},
    {"gpu": "h200-discounted", "concurrency": 500, "api": "scaleway"},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_port_matches_python_on_the_same_inputs(tmp_path) -> None:
    payload = sizing.build_payload()
    script = tmp_path / "run.cjs"
    script.write_text(
        "const S = require(process.argv[2]);\n"
        "const {payload, cases} = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "console.log(JSON.stringify(cases.map((c) => S.size(c, payload))));\n"
    )
    proc = subprocess.run(
        ["node", str(script), str(JS)],
        input=json.dumps({"payload": payload, "cases": CASES}),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    js_results = json.loads(proc.stdout)
    assert len(js_results) == len(CASES)
    for case, js in zip(CASES, js_results, strict=True):
        py = sizing.size(case, payload)
        pf, jf = sizing.flatten(py), sizing.flatten(js)
        assert pf.keys() == jf.keys(), case
        for key, pv in pf.items():
            jv = jf[key]
            if isinstance(pv, bool) or isinstance(jv, bool):
                assert pv == jv, (case, key)
            else:
                assert jv == pytest.approx(pv, rel=1e-9, abs=1e-9), (case, key, pv, jv)
        assert py["feasibility"]["reasons"] == js["feasibility"]["reasons"], case


def test_js_port_detects_a_drift() -> None:
    """The parity comparison above must be able to fail: a perturbed Python value is caught."""
    py = sizing.flatten(sizing.size({}))
    off = dict(py)
    off["kv.bytes_per_token"] = py["kv.bytes_per_token"] * 1.001
    assert off["kv.bytes_per_token"] != pytest.approx(py["kv.bytes_per_token"], rel=1e-9)


# ---- CLI ------------------------------------------------------------------------------------------------------


def test_cli_size_writes_envelope_and_exit_codes(tmp_path) -> None:
    app = build_app()
    ok = runner.invoke(app, ["deploy", "size", "--out", str(tmp_path), "--context", "128k", "--concurrency", "64"])
    assert ok.exit_code == 0, ok.output
    assert "44.9 KB" in ok.output and "FEASIBLE" in ok.output
    bad = runner.invoke(app, ["deploy", "size", "--out", str(tmp_path), "--context", "1m"])
    assert bad.exit_code == 1 and "INFEASIBLE" in bad.output
    files = sorted(tmp_path.glob("*/result.json"))
    assert len(files) == 2
    env = json.loads(files[0].read_text())
    assert env["component"] == "deploy" and env["data"]["heuristic"] is True


def test_cli_validate_fails_on_perturbed_config() -> None:
    app = build_app()
    assert runner.invoke(app, ["deploy", "validate"]).exit_code == 0
    r = runner.invoke(app, ["deploy", "validate", "--set", "kv_lora_rank=1024"])
    assert r.exit_code == 1 and "FAIL  kv_bytes_per_token_fp8" in r.output


def test_cli_rejects_flash_and_catalog_lists_sources() -> None:
    app = build_app()
    assert runner.invoke(app, ["deploy", "size", "--model", "glm-5.3-flash", "--no-write"]).exit_code == 2
    cat = runner.invoke(app, ["deploy", "catalog"])
    assert "https://nebius.com/prices" in cat.output and "2026-10-26" in cat.output
