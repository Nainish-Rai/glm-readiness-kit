"""Eval engine against the mock: planted quality is recovered, runs resume, throttling is not failure, budgets stop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.contracts.profile import load_profile_file
from glmkit.eval.demo import build_profiles, write_demo
from glmkit.eval.pricing import PriceTable
from glmkit.eval.runner import EvalError, EvalInterrupted, EvalOptions, run_eval
from glmkit.results import load_envelopes

BASE = "demo-incumbent"


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory) -> dict[str, Path]:
    return write_demo(tmp_path_factory.mktemp("demo"))


@pytest.fixture
def demo(demo_dir, monkeypatch) -> dict[str, Path]:
    monkeypatch.setenv("GLMKIT_PROFILES_DIRS", str(demo_dir["profiles"]))  # conftest clears it for every test
    return demo_dir


def opts(demo, out: Path, **kw) -> EvalOptions:
    base = {
        "set_path": demo["set"],
        "targets": ["demo-glm-equal", "demo-glm-worse"],
        "baseline": BASE,
        "prices": [demo["prices"]],
        "out_dir": out,
        "seed": 7,
        "n_boot": 1000,
        "concurrency": 16,
    }
    return EvalOptions(**{**base, **kw})


def row(env, target: str, tt: str = "ALL") -> dict:
    return next(c for c in env.data["comparisons"] if c["target"] == target and c["task_type"] == tt)


@pytest.fixture(scope="module")
def planted(demo_dir, tmp_path_factory):
    import asyncio
    import os

    os.environ["GLMKIT_PROFILES_DIRS"] = str(demo_dir["profiles"])
    try:
        return asyncio.run(run_eval(opts(demo_dir, tmp_path_factory.mktemp("planted"), judge="demo-judge")))
    finally:
        os.environ.pop("GLMKIT_PROFILES_DIRS", None)


def test_planted_quality_is_recovered_at_fixed_seed(planted) -> None:
    env = planted.envelope
    worse, equal = row(env, "demo-glm-worse")["quality"], row(env, "demo-glm-equal")["quality"]
    assert worse["verdict"] == "inferior" and worse["ci"][1] < -0.05  # planted 25% vs 3% errors
    assert equal["verdict"] == "non_inferior" and equal["ci"][0] > -0.05
    # the planted gap is recovered: worse is roughly 22 points below the baseline on binary task types
    clf = row(env, "demo-glm-worse", "classify")["quality"]
    assert -0.35 < clf["delta"] < -0.1 and clf["verdict"] == "inferior"
    assert env.verdict == "fail" and env.component == "eval"


def test_envelope_contents(planted) -> None:
    env = planted.envelope
    d = env.data
    assert d["set"]["items"] == 340 and set(d["set"]["task_types"]) == {
        "classify",
        "arithmetic",
        "extract",
        "text_to_sql",
        "summarize",
    }
    s = d["target_summary"]
    assert s[BASE]["cost_per_1000_usd"] > 10 * s["demo-glm-equal"]["cost_per_1000_usd"]  # incumbent is priced higher
    assert row(env, "demo-glm-equal")["cost"]["ratio"] < 0.2
    assert row(env, "demo-glm-equal", "classify")["mcnemar"]["p"] <= 1
    assert d["price_table"]["entries"] and d["regressions"]["demo-glm-worse"]["worst"]
    assert any(r["critical"] for r in d["regressions"]["demo-glm-worse"]["critical"])
    # envelope round-trips through the loader the dashboard uses
    found, warnings = load_envelopes(planted.run_dir)
    assert found and not warnings


def test_judge_audit_reports_swap_disagreement(planted) -> None:
    j = planted.envelope.data["judge"]
    assert j["judge_family"] == "mock-judge" and not j["family_overlap"]
    p = j["per_target"]["demo-glm-equal"]
    assert p["judged"] == 40 and p["position_inconsistent"] > 0  # planted 15% first-slot bias
    assert p["position_inconsistency_rate"] == p["position_inconsistent"] / p["judged"]
    flagged = [
        it
        for it in planted.envelope.items
        if it.target == "demo-glm-equal" and it.data["judged"]
        for r in it.data["replays"]
        if "position_inconsistent" in r["grade"].get("flags", [])
    ]
    assert len(flagged) == p["position_inconsistent"]
    inc = flagged[0]
    assert inc is not None


async def test_judge_family_overlap_warning(demo, tmp_path) -> None:
    prof = build_profiles()["demo-judge"] | {"name": "glm-judge", "model": "glm-5.3-flash"}
    (demo["profiles"] / "glm-judge.yaml").write_text(yaml.safe_dump(prof))
    o = opts(demo, tmp_path, judge="glm-judge", task_types=["summarize"], targets=["demo-glm-equal"])
    env = (await run_eval(o)).envelope
    assert "demo-glm-equal" in env.data["judge"]["family_overlap"] and env.data["judge"]["warnings"]


async def test_resume_equals_uninterrupted(demo, tmp_path) -> None:
    kw = {"task_types": ["classify", "arithmetic"], "max_items": 60, "targets": ["demo-glm-worse"], "k": 2}
    full = (await run_eval(opts(demo, tmp_path / "full", run_id="full", **kw))).envelope
    with pytest.raises(EvalInterrupted):
        await run_eval(opts(demo, tmp_path / "part", run_id="part", interrupt_after=70, **kw))
    calls = (tmp_path / "part" / "part" / "calls.jsonl").read_text().splitlines()
    assert 70 <= len(calls) < 240
    with pytest.raises(EvalError, match="already exists"):
        await run_eval(opts(demo, tmp_path / "part", run_id="part", **kw))
    resumed = (await run_eval(opts(demo, tmp_path / "part", run_id="part", resume=True, **kw))).envelope
    assert resumed.data["run"]["resumed_calls"] >= 70 and resumed.data["run"]["new_calls"] > 0

    def strip(env):
        out = []
        for c in env.data["comparisons"]:
            out.append(
                (
                    c["task_type"],
                    c["quality"],
                    c["mcnemar"],
                    c["reliability"],
                    c["cost"]["mean_diff"],
                    c["cost"]["ratio"],
                )
            )
        return out, [(i.id, i.status, i.metrics["score"]) for i in env.items]

    assert strip(resumed) == strip(full)


async def test_resume_refuses_a_different_set(demo, tmp_path) -> None:
    kw = {"task_types": ["classify"], "max_items": 25, "targets": ["demo-glm-equal"]}
    await run_eval(opts(demo, tmp_path, run_id="r", **kw))
    with pytest.raises(EvalError, match="different golden set"):
        await run_eval(opts(demo, tmp_path, run_id="r", resume=True, **{**kw, "max_items": 26}))
    with pytest.raises(EvalError, match="nothing to resume"):
        await run_eval(opts(demo, tmp_path, run_id="nope", resume=True, **kw))


async def test_throttling_is_retried_and_never_a_failure(demo, tmp_path) -> None:
    prof = yaml.safe_load((demo["profiles"] / "demo-glm-equal.yaml").read_text())
    prof["name"] = "demo-glm-throttled"
    prof["headers"] |= {
        "X-GLMKit-Defects": "eval-oracle,degrade-quality,rate-limit-burst",
        "X-GLMKit-Throttle-Every": "3",
    }
    (demo["profiles"] / "demo-glm-throttled.yaml").write_text(yaml.safe_dump(prof))
    kw = {"task_types": ["classify"], "max_items": 40, "targets": ["demo-glm-throttled", "demo-glm-equal"]}
    env = (await run_eval(opts(demo, tmp_path, **kw))).envelope
    t = env.data["target_summary"]["demo-glm-throttled"]
    assert t["throttled_retries"] > 0 and t["call_errors"] == 0 and t["throttled_final"] == 0
    # retried calls land on the same answers: identical quality to the same target without throttling
    same = env.data["target_summary"]["demo-glm-equal"]
    assert t["pass_rate"] == pytest.approx(same["pass_rate"], abs=0.2)
    assert not [i for i in env.items if i.status == "throttled"]


async def test_exhausted_retries_are_throttled_not_failed(demo, tmp_path) -> None:
    prof = yaml.safe_load((demo["profiles"] / "demo-glm-equal.yaml").read_text())
    prof["name"] = "demo-glm-always429"
    prof["headers"] |= {"X-GLMKit-Defects": "eval-oracle,rate-limit-burst", "X-GLMKit-Throttle-Every": "1"}
    (demo["profiles"] / "demo-glm-always429.yaml").write_text(yaml.safe_dump(prof))
    kw = {"task_types": ["classify"], "max_items": 25, "targets": ["demo-glm-always429"], "max_retries": 1}
    env = (await run_eval(opts(demo, tmp_path, **kw))).envelope
    items = [i for i in env.items if i.target == "demo-glm-always429"]
    assert items and all(i.status == "throttled" for i in items)
    assert env.data["target_summary"]["demo-glm-always429"]["call_errors"] == 0
    assert row(env, "demo-glm-always429")["quality"]["n"] == 0


async def test_budget_cap_stops_new_calls(demo, tmp_path) -> None:
    env = (await run_eval(opts(demo, tmp_path, budget_usd=0.002, concurrency=1, task_types=["classify"]))).envelope
    b = env.data["budget"]
    assert b["exhausted"] and b["calls_skipped"] > 0 and b["spent_usd"] < 0.01
    assert env.verdict == "incomplete"


async def test_unpriced_model_is_not_logged_never_zero(demo, tmp_path) -> None:
    o = opts(demo, tmp_path, prices=[], task_types=["classify"], max_items=25, targets=["demo-glm-equal"])
    env = (await run_eval(o)).envelope  # the incumbent mock model has no price without the demo table
    assert env.data["target_summary"][BASE]["cost_per_1000_usd"] is None
    assert row(env, "demo-glm-equal")["cost"] is None
    assert any("no price entry" in w for w in env.data["warnings"])


def test_price_table_cost_and_provenance(tmp_path) -> None:
    t = PriceTable.load()
    usage = {"input_tokens_total": 1_000_000, "cache_read_tokens": 400_000, "output_tokens_total": 100_000}
    # 600k uncached x 1.40 + 400k cached x 0.26 + 100k out x 4.40 = 0.84 + 0.104 + 0.44
    assert t.cost_usd("glm-5.3", "zai", usage) == pytest.approx(1.384)
    assert t.cost_usd("glm-5.3", "mistral", usage) == pytest.approx(0.84 + 0.056 + 0.44)
    assert t.cost_usd("unknown-model", None, usage) is None
    for e in t.describe()["entries"]:
        assert e["source_url"].startswith("https://") and e["as_of"]
    extra = tmp_path / "p.yaml"
    extra.write_text(
        yaml.safe_dump(
            {
                "version": "x",
                "entries": [
                    {"model": "inc", "input": 1, "output": 2, "source_url": "https://e", "as_of": "2026-01-01"}
                ],
            }
        )
    )
    assert PriceTable.load(extra).cost_usd("inc", None, {"input_tokens_total": 1_000_000}) == 1.0


async def test_switchtrace_jsonl_becomes_replayable_items(demo, tmp_path) -> None:
    from glmkit.contracts.trace import SwitchTrace  # noqa: F401  (schema reference)

    rows = []
    for i in range(24):
        rows.append(
            {
                "schema": "switch-eval.call/0",
                "call_id": f"call-{i}",
                "trace_id": f"sess-{i // 3}",
                "ts_start": "2026-10-26T00:00:00Z",
                "task_type": "classify",
                "source": {"kind": "openai", "ref": f"f#{i}", "adapter_version": "0"},
                "request": {
                    "provider": "openai",
                    "model": "gpt-x",
                    "messages": [
                        {"role": "user", "parts": [{"type": "text", "text": f"Q{i} <mock-answer>a{i}</mock-answer>"}]}
                    ],
                },
                "labels": {"expected": f"a{i}"},
            }
        )
    p = tmp_path / "traces.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    from glmkit.eval.goldenset import load_golden_set

    gs = load_golden_set(p)
    assert (
        len(gs.items) == 24 and gs.items[0].cluster_id == "sess-0" and gs.grader_for(gs.items[0]) == {"type": "exact"}
    )
    env = (await run_eval(opts(demo, tmp_path / "out", set_path=p, targets=["demo-glm-equal"]))).envelope
    assert row(env, "demo-glm-equal")["quality"]["n_clusters"] == 8


def test_cli_run_and_demo(demo, tmp_path) -> None:
    r = CliRunner().invoke(
        build_app(),
        [
            "eval",
            "run",
            "--set",
            str(demo["set"]),
            "--baseline",
            BASE,
            "--targets",
            "demo-glm-equal,demo-glm@low",
            "--prices",
            str(demo["prices"]),
            "--task-type",
            "classify",
            "--max-items",
            "30",
            "--out",
            str(tmp_path / "o"),
            "--bootstrap",
            "300",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "demo-glm@low" in r.output and "verdict:" in r.output
    bad = CliRunner().invoke(build_app(), ["eval", "run", "--set", "nope.yaml", "--baseline", BASE, "--targets", "x"])
    assert bad.exit_code == 2
    assert load_profile_file(demo["profiles"] / "demo-judge.yaml").model == "mock-judge-1"


async def test_no_secrets_in_outputs(demo, tmp_path, monkeypatch) -> None:
    secret = "sk-test-SECRET-1234567890"
    monkeypatch.setenv("GLMKIT_MOCK_API_KEY", secret)
    prof = yaml.safe_load((demo["profiles"] / "demo-glm-equal.yaml").read_text())
    prof |= {"name": "demo-keyed", "api_key_env": "GLMKIT_MOCK_API_KEY", "api_key_required": True}
    (demo["profiles"] / "demo-keyed.yaml").write_text(yaml.safe_dump(prof))
    res = await run_eval(opts(demo, tmp_path, task_types=["classify"], max_items=20, targets=["demo-keyed"]))
    for f in res.run_dir.rglob("*"):
        if f.is_file():
            assert secret not in f.read_text(errors="ignore"), f
