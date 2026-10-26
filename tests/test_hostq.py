"""Host-quality slice (issue 05): statistics, versioned data, reference, every gate against its planted defect."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.evidence import (
    Exchange,
    HttpRequestRecord,
    HttpResponseRecord,
    NormalizedResponse,
    NormUsage,
    Timing,
)
from glmkit.contracts.profile import get_profile
from glmkit.hostq import analysis, probes, stats
from glmkit.hostq.engine import RECORD_PATTERNS, HostOptions, record_reference, run_host
from glmkit.hostq.reference import ReferenceFileError, load_reference
from glmkit.mock.defects.tool_ttft_penalty import ToolTtftPenalty

REPO = Path(__file__).resolve().parent.parent
SEED = 20261026


# ------------------------------------------------------------------ statistics


def _sample(rng: np.random.Generator, probs: list[float], n_prompts: int = 8, per: int = 6):
    return [(f"p{i}", int(rng.choice(len(probs), p=probs))) for i in range(n_prompts) for _ in range(per)]


def test_mmd_same_distribution_rarely_rejects() -> None:
    rng = np.random.default_rng(1)
    hits = sum(
        stats.mmd_permutation_test(
            _sample(rng, [0.4, 0.3, 0.2, 0.1]),
            _sample(rng, [0.4, 0.3, 0.2, 0.1]),
            stats.categorical_kernel,
            n_perm=199,
            seed=i,
        ).p_value
        < 0.05
        for i in range(40)
    )
    assert hits <= 6  # nominal 5% of 40 = 2


def test_mmd_detects_shift_and_is_seeded() -> None:
    rng = np.random.default_rng(2)
    a, b = _sample(rng, [0.7, 0.1, 0.1, 0.1]), _sample(rng, [0.1, 0.1, 0.1, 0.7])
    r1 = stats.mmd_permutation_test(a, b, stats.categorical_kernel, n_perm=499, seed=7)
    r2 = stats.mmd_permutation_test(a, b, stats.categorical_kernel, n_perm=499, seed=7)
    assert r1.p_value < 0.01 and r1 == r2


def test_hamming_kernel_and_ratio_ci() -> None:
    k = stats.hamming_kernel(4)
    assert k("abcd", "abcd") == 1.0 and k("abcd", "abxd") == 0.75
    same = stats.ratio_ci(24, 24, 24, 24)
    assert same["ratio"] == 1.0 and same["hi"] >= 1.0
    low = stats.ratio_ci(15, 24, 24, 24)
    assert low["hi"] < 1.0
    assert stats.ratio_ci(1, 2, 0, 2)["ratio"] is None
    lo, hi = stats.wilson(20, 24)
    assert 0.6 < lo < 20 / 24 < hi < 1


def test_median_shift_test() -> None:
    slow = stats.median_shift_test([0.1, 0.11, 0.12, 0.1, 0.13], [1.6, 1.5, 1.7, 1.6, 1.55], seed=1)
    assert slow["delta"] > 1.3 and slow["p_value"] < 0.05
    same = stats.median_shift_test([0.1, 0.2, 0.15, 0.12], [0.11, 0.19, 0.16, 0.13], seed=1)
    assert same["p_value"] > 0.05


# ------------------------------------------------------------------ latency accounting


def _ex(label: str, status: int = 200, ttft: float | None = 100.0, total: float = 600.0, toks: int = 50) -> Exchange:
    return Exchange(
        id="e",
        label=label,
        protocol="openai_chat",
        mode="stream",
        request=HttpRequestRecord(url="u", body={"model": "glm-5.3"}),
        response=HttpResponseRecord(status=status),
        normalized=NormalizedResponse(http_status=status, usage=NormUsage(completion_tokens=toks)),
        timing=Timing(ttft_ms=ttft, total_ms=total),
    )


def test_throttled_calls_are_excluded_from_latency_and_counted() -> None:
    attempts = [
        [_ex("lat.no_tools", ttft=100)],
        [_ex("lat.no_tools", status=429, ttft=9000)],
        [_ex("lat.no_tools", ttft=300)],
    ]
    row = analysis.latency_by_label(attempts, "lat.")["no_tools"]
    assert row["throttled"] == 1 and row["requests"] == 3
    assert row["ttft_s"]["n"] == 2 and row["ttft_s"]["p50"] == pytest.approx(0.2)
    assert row["ttft_s"]["p90"] == pytest.approx(0.28)
    assert row["decode_tps"]["p50"] == pytest.approx(50 / 0.4, rel=0.2)


# ------------------------------------------------------------------ versioned data and reference


def test_probe_set_id_changes_with_content(tmp_path: Path) -> None:
    for f in probes.SETS.values():
        shutil.copy(probes.DATA_DIR / f, tmp_path / f)
    before = probes.probe_set_ids(data_dir=tmp_path)
    assert before == probes.probe_set_ids()
    p = tmp_path / probes.SETS["action"]
    data = json.loads(p.read_text())
    data["prompts"][0]["prompt"] += " Please hurry."
    p.write_text(json.dumps(data))
    probes._load_cached.cache_clear()
    after = probes.probe_set_ids(data_dir=tmp_path)
    probes._load_cached.cache_clear()
    assert after["action"] != before["action"] and after["text"] == before["text"]


@pytest.fixture(scope="module")
def reference_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    outcome = asyncio.run(
        run_host(get_profile("mock"), HostOptions(patterns=list(RECORD_PATTERNS), samples=3, write=False, label="ref"))
    )
    ref = record_reference(outcome, "ref", 3)
    return ref.save(tmp_path_factory.mktemp("ref") / "ref.json")


def test_reference_id_tracks_probe_sets_and_content(reference_file: Path) -> None:
    ref = load_reference(reference_file)
    assert len(ref.reference_id) == 16 and ref.probe_sets == probes.probe_set_ids()
    other = ref.model_copy(update={"probe_sets": {**ref.probe_sets, "action": "0" * 12}})
    assert other.content_id() != ref.reference_id
    raw = json.loads(reference_file.read_text())
    raw["probe_sets"]["action"] = "0" * 12  # recorded with other probes (id re-sealed)
    bad = reference_file.with_name("stale.json")
    stale = ref.model_copy(update={"probe_sets": raw["probe_sets"]}).seal()
    stale.save(bad)
    with pytest.raises(ReferenceFileError, match="other probe sets"):
        load_reference(bad)
    raw["canary"]["correct"] = 1  # hand edit without re-sealing
    edited = reference_file.with_name("edited.json")
    edited.write_text(json.dumps(raw))
    with pytest.raises(ReferenceFileError, match="edited"):
        load_reference(edited, check_probes=False)


# ------------------------------------------------------------------ each gate against its planted defect


def host(reference: Path | None, *defects: str, **kw):
    ref = load_reference(reference) if reference else None
    opts = HostOptions(reference=ref, mock_defects=list(defects), write=False, samples=3, **kw)
    return asyncio.run(run_host(get_profile("mock"), opts)).envelope


def gate(env, case_id: str) -> dict:
    return next(g for g in env.data["host_quality"]["gates"] if g["id"] == case_id)


def test_clean_mock_against_clean_reference_is_not_flagged(reference_file: Path) -> None:
    env = host(reference_file, patterns=["FP-*"])
    hq = env.data["host_quality"]
    assert hq["verdict"] == "pass", hq["gates"]
    assert hq["sections"]["FP-002"]["p_value"] > 0.01 and hq["sections"]["FP-003"]["p_value"] > 0.01
    assert env.component == "hostq"


def test_quantized_mock_is_flagged_with_small_p(reference_file: Path) -> None:
    env = host(reference_file, "quantized", patterns=["FP-*"])
    hq = env.data["host_quality"]
    assert hq["verdict"] == "fail"
    for case in ("FP-002", "FP-003"):
        assert gate(env, case)["status"] == "flag"
        assert hq["sections"][case]["p_value"] < 0.01 and hq["sections"][case]["seed"] == SEED
    assert gate(env, "FP-004")["status"] == "flag" and gate(env, "FP-005")["status"] == "flag"
    assert hq["sections"]["FP-005"]["percent_of_reference"]["hi"] < 1.0


def test_wrong_model_field_is_flagged() -> None:
    assert gate(host(None, patterns=["FP-001"]), "FP-001")["status"] == "pass"
    env = host(None, "wrong-model-field", patterns=["FP-001"])
    assert gate(env, "FP-001")["status"] == "flag"
    assert env.data["host_quality"]["verdict"] == "fail"


def test_without_reference_gates_are_not_evaluated_and_verdict_incomplete() -> None:
    env = host(None, patterns=["FP-002", "FP-005"])
    hq = env.data["host_quality"]
    assert {g["status"] for g in hq["gates"]} == {"not_evaluated"} and hq["verdict"] == "incomplete"
    assert hq["sections"]["FP-005"]["correct"] == hq["sections"]["FP-005"]["n"]  # absolute score still kept


def test_tool_ttft_penalty_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ToolTtftPenalty, "penalty_s", 0.5)
    case = yaml.safe_load((REPO / "cases/latency/LAT-001-ttft-with-tools.yaml").read_text())
    case["executor_config"]["prompt_tokens"] = 500
    case["expect"]["default"][0]["max_delta_s"] = 0.2
    (tmp_path / "LAT-001-ttft-with-tools.yaml").write_text(yaml.safe_dump(case))
    kw = {"patterns": ["LAT-001"], "case_roots": [tmp_path], "latency_n": 4}
    clean = host(None, **kw)
    assert gate(clean, "LAT-001")["status"] == "pass"
    bad = host(None, "tool-ttft-penalty", **kw)
    g = gate(bad, "LAT-001")
    assert g["status"] == "flag" and g["severity"] == "soft"
    assert bad.data["host_quality"]["verdict"] == "warn"
    tools = bad.data["host_quality"]["sections"]["LAT-001"]["conditions"]
    assert tools["tools_60"]["ttft_s"]["n"] == 4 and tools["tools_60"]["ttft_s"]["p90"] > 0.5


def test_latency_effort_records_medians_and_tokens_per_second() -> None:
    env = host(None, patterns=["LAT-002"], latency_n=3)
    eff = env.data["host_quality"]["sections"]["LAT-002"]["efforts"]
    assert set(eff) == {"low", "high", "max"}
    assert eff["high"]["ttft_s"]["n"] == 3 and eff["high"]["throttled"] == 0
    assert eff["high"]["e2e_tps"]["p50"] > 0


def test_host_quality_never_enters_the_correctness_verdict() -> None:
    cases = REPO / "cases"
    out = asyncio.run(run_conformance(get_profile("mock"), RunOptions(write=False)))
    assert out.envelope.component == "conform"
    assert not [i for i in out.envelope.items if i.group.startswith(("LAT-", "FP-"))]
    assert all(
        yaml.safe_load(p.read_text())["opt_in"] is True
        for p in [*cases.glob("latency/*.yaml"), *cases.glob("fingerprint/*.yaml")]
    )


def test_hosts_page_collects_latest_run_per_host(reference_file: Path) -> None:
    from glmkit.report.pages.hosts import HostsPage
    from glmkit.report.registry import RunRef

    runs = [RunRef(host(reference_file, patterns=["FP-002", "FP-005"], label=f"h{i}"), f"r{i}.json") for i in range(3)]
    data = HostsPage().collect(runs)
    assert data and data["count"] == 3 and data["hosts"][0]["action"]["p_value"] > 0.01
