"""Cost and cache forensics (issue 12): attribution of planted breaks, price table, projection math, tab."""

from __future__ import annotations

import json
from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from glmkit.contracts.trace import SwitchTrace, TraceUsage
from glmkit.cost import forensics as fx
from glmkit.cost.analyze import analyze
from glmkit.cost.prices import SHIPPED, PriceEntry, PriceTable, load_price_table, price_for
from glmkit.cost.projection import Workload, cost_usd, project
from glmkit.cost.synth import EXPECTED_FIRST_BREAK, SCENARIOS, build_session, demo_token_counts, demo_traces


# ------------------------------------------------------------------ attribution of planted cache breaks
@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s != "healthy"])
def test_planted_break_is_attributed_to_its_cause(scenario: str) -> None:
    r = fx.analyze_trace(build_session(scenario))
    assert r["first_break_seq"] == EXPECTED_FIRST_BREAK.get(scenario, 3)
    assert r["first_break_cause"] == SCENARIOS[scenario]
    assert r["lost_tokens"] > 0


def test_healthy_session_has_no_break() -> None:
    r = fx.analyze_trace(build_session("healthy"))
    assert r["breaks"] == 0 and r["first_break_cause"] is None and r["cache_share"] > 0.7


def test_reasoning_subtypes_and_only_planted_turn_breaks() -> None:
    dropped = fx.analyze_trace(build_session("reasoning-dropped"))
    edited = fx.analyze_trace(build_session("reasoning-edited"))
    assert dropped["breaks"] == edited["breaks"] == 1
    assert dropped["turns"][3]["causes"][0]["subtype"] == "dropped"
    assert edited["turns"][3]["causes"][0]["subtype"] == "edited"


def test_disabled_detector_loses_its_attribution() -> None:
    calls = build_session("tool-reorder")
    assert fx.analyze_trace(calls)["first_break_cause"] == "tool_order_change"
    off = fx.analyze_trace(calls, disabled={"tool_order"})
    assert off["first_break_cause"] == fx.UNATTRIBUTED and off["breaks"] == 1


def test_disable_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(fx.DISABLE_ENV, "timestamp_in_prefix")
    assert fx.analyze_trace(build_session("timestamp-in-prefix"))["first_break_cause"] == fx.UNATTRIBUTED


def test_missing_usage_is_not_a_break() -> None:
    calls = build_session("tool-reorder")
    for c in calls:
        c.usage = TraceUsage()
    r = fx.analyze_trace(calls)
    assert r["breaks"] == 0 and r["cache_share"] is None


# ------------------------------------------------------------------ price table
def test_shipped_price_table_entries_have_source_and_date() -> None:
    t = load_price_table()
    assert t.prices and t.coding_plan.tiers
    for e in t.prices:
        assert e.source.startswith("http") and isinstance(e.retrieved, date)
    assert price_for("glm-5.3").cached_input == 0.26  # type: ignore[union-attr]
    assert price_for("glm-5.3", "bedrock").input is None  # type: ignore[union-attr]
    assert price_for("nope") is None


def _raw() -> dict:
    return yaml.safe_load(SHIPPED.read_text())


@pytest.mark.parametrize("drop", ["source", "retrieved"])
def test_price_entry_without_source_or_date_fails(drop: str) -> None:
    raw = _raw()
    del raw["prices"][0][drop]
    with pytest.raises(ValidationError):
        PriceTable.model_validate(raw)


def test_price_entry_with_non_url_source_fails() -> None:
    raw = _raw()
    raw["coding_plan"]["tiers"][0]["source"] = "trust me"
    with pytest.raises(ValidationError):
        PriceTable.model_validate(raw)


def test_prices_env_override(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _raw()
    raw["prices"][0]["input"] = 9.0
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(raw))
    monkeypatch.setenv("GLMKIT_PRICES", str(p))
    assert load_price_table().lookup("glm-5.3").input == 9.0  # type: ignore[union-attr]


# ------------------------------------------------------------------ projection math (hand computed)
E = PriceEntry(
    host="h", model="m", input=1.4, cached_input=0.26, output=4.4, source="https://x", retrieved=date(2026, 10, 26)
)


def test_cost_with_cached_input_pricing() -> None:
    # 1,000,000 input of which 600,000 cached, 100,000 output:
    # fresh 400,000 * 1.4/M = 0.56; cached 600,000 * 0.26/M = 0.156; output 100,000 * 4.4/M = 0.44; total 1.156
    w = Workload(1, 1, 0, 1_000_000, 600_000, 0, 100_000, 0)
    c = cost_usd(w, E)
    assert c["parts"] == pytest.approx({"fresh_input": 0.56, "cached_input": 0.156, "output": 0.44})
    assert c["usd"] == pytest.approx(1.156)


def test_cost_without_cached_price_gives_none_and_upper_bound() -> None:
    e = E.model_copy(update={"cached_input": None})
    w = Workload(1, 1, 0, 1_000_000, 600_000, 0, 100_000, 0)
    c = cost_usd(w, e)
    assert c["usd"] is None and c["missing"] == ["cached_input"]
    assert c["usd_upper_bound"] == pytest.approx(1.4 + 0.44)  # all 1M input at 1.4 + output 0.44
    # no cache reads: cached price is irrelevant
    assert cost_usd(Workload(1, 1, 0, 1_000_000, 0, 0, 0, 0), e)["usd"] == pytest.approx(1.4)


def test_projection_cache_fixed_and_plan_break_even() -> None:
    table = load_price_table()
    # 2 tasks, 2M input (1M cached), 200k output, 500k cached tokens lost to a break
    w = Workload(2, 4, 0, 2_000_000, 1_000_000, 0, 200_000, 0, lost_cache_tokens=500_000)
    p = project(w, table, ["glm-5.3"], credits_per_usd=100, tasks_per_month=100)
    zai = next(r for r in p["payg"] if r["host"] == "zai-api")
    # fresh 1M*1.4 + cached 1M*0.26 + out 0.2M*4.4 = 1.4 + 0.26 + 0.88 = 2.54
    assert zai["usd"] == pytest.approx(2.54) and zai["usd_per_task"] == pytest.approx(1.27)
    # fixed: fresh 0.5M*1.4 + cached 1.5M*0.26 + 0.88 = 0.7 + 0.39 + 0.88 = 1.97
    assert zai["usd_if_cache_fixed"] == pytest.approx(1.97)
    lite = p["coding_plan"]["tiers"][0]
    assert lite["break_even_tasks_per_month"] == pytest.approx(18 / 1.27)
    assert lite["credits_per_task"] == pytest.approx(127.0)  # 1.27 USD * 100 credits/USD
    assert lite["tasks_per_5h"]["peak"] == pytest.approx(2000 / 127)
    assert lite["tasks_per_5h"]["off_peak"] == pytest.approx(2000 / 63.5)  # published 0.5x off-peak
    assert lite["tasks_per_5h"]["peak_reported"] == pytest.approx(2000 / 381)  # heuristic 3x
    assert lite["usd_per_month_yearly"] == pytest.approx(12.6)
    br = next(r for r in p["payg"] if r["host"] == "bedrock")
    assert br["usd"] is None and br["usd_upper_bound"] is None


def test_plan_projection_without_conversion_is_not_logged() -> None:
    w = Workload(1, 1, 0, 1000, 0, 0, 10, 0)
    row = project(w, load_price_table(), ["glm-5.3"])["coding_plan"]["tiers"][0]
    assert row["credits_per_task"] is None and row["tasks_per_5h"] is None


# ------------------------------------------------------------------ verbosity and tokenizer
def test_verbosity_measured_and_heuristic_savings() -> None:
    res = analyze(demo_traces(), counts=demo_token_counts())
    sav = {(s["from_effort"], s["to_effort"]): s for s in res["verbosity"]["savings"]}
    assert sav[("max", "low")]["basis"] == "measured" and sav[("max", "low")]["factor"] < 0.5
    only_max = [t for t in demo_traces() if t.trace_id.startswith("synthetic-effort-max")]
    h = analyze(only_max)["verbosity"]["savings"]
    assert [(s["to_effort"], s["basis"], s["factor"]) for s in h if s["to_effort"] == "high"] == [
        ("high", "heuristic", 0.5)
    ]


def test_tokenizer_inflation_by_content_type() -> None:
    rows = [
        {"content_type": "json", "glm_tokens": 120, "incumbent_tokens": 100},
        {"content_type": "json", "glm_tokens": 130, "incumbent_tokens": 100},
        {"text": "今天天气很好，我们去公园散步吧。" * 5, "glm_tokens": 50, "incumbent_tokens": 100},
        {"text": '{"a": 1}', "glm_tokens": 5, "incumbent_tokens": 4},
    ]
    r = {x["content_type"]: x for x in fx.tokenizer_inflation(rows)["by_type"]}
    assert r["json"]["samples"] == 3 and r["json"]["ratio"] == pytest.approx(255 / 204)
    assert r["cjk"]["ratio"] == pytest.approx(0.5) and r["prose"]["ratio"] is None


def test_causes_summary_and_wasted_usd() -> None:
    res = analyze(build_session("tool-reorder"))
    (c,) = res["causes"]
    assert c["cause"] == "tool_order_change" and c["breaks"] == 1
    assert c["usd_wasted"] == pytest.approx(c["lost_tokens"] / 1e6 * (1.4 - 0.26))


# ------------------------------------------------------------------ CLI and tab
def test_cli_writes_envelope_and_tab_renders(tmp_path) -> None:
    from typer.testing import CliRunner

    from glmkit.cli import build_app
    from glmkit.report.builder import build_site

    runner = CliRunner()
    app = build_app()
    r = runner.invoke(app, ["cost", "demo", "-o", str(tmp_path / "demo")])
    assert r.exit_code == 0, r.output
    res = tmp_path / "results"
    r = runner.invoke(
        app,
        [
            "cost",
            "analyze",
            str(tmp_path / "demo/traces.jsonl"),
            "--counts",
            str(tmp_path / "demo/counts.jsonl"),
            "-o",
            str(res),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "first break at call 3: tool_order_change" in r.output and "not logged" in r.output
    env = json.loads(next(res.glob("*/result.json")).read_text())
    assert env["component"] == "cost" and env["verdict"] == "warn"
    site = tmp_path / "site"
    built = build_site(res, site)
    assert "cost" in built.pages and built.findings == []
    data = json.loads((site / "data" / "cost.json").read_text())
    assert data["totals"]["breaks"] == 13 and data["turns"]["synthetic-tool-reorder"][3]["broke"]


def test_cli_rejects_bad_trace_file(tmp_path) -> None:
    from typer.testing import CliRunner

    from glmkit.cli import build_app

    bad = tmp_path / "t.jsonl"
    bad.write_text('{"not": "a trace"}\n')
    r = CliRunner().invoke(build_app(), ["cost", "analyze", str(bad), "-o", str(tmp_path)])
    assert r.exit_code == 2 and "not a SwitchTrace" in r.output


def test_traces_roundtrip_through_jsonl() -> None:
    t = build_session("healthy")[0]
    assert SwitchTrace.model_validate_json(t.to_jsonl()).call_id == t.call_id
