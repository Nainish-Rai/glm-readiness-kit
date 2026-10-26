"""Trace importers (issue 11): golden outputs, usage normalisation, redaction, schema, CLI, registry."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.contracts.trace import SwitchTrace
from glmkit.traces.redaction import TraceRedactor
from glmkit.traces.registry import (
    Adapter,
    ImportContext,
    TaskMapper,
    adapters,
    get_adapter,
    read_records,
    register_adapter,
)
from glmkit.traces.stats import compute_stats
from tests.conftest import REPO

FIX = Path(__file__).parent / "fixtures" / "traces"
GOLDEN = FIX / "golden"
CASES = [
    ("otel", "otel.jsonl"),
    ("langfuse", "langfuse.jsonl"),
    ("langfuse", "langfuse.csv"),
    ("openai", "openai.jsonl"),
    ("anthropic", "anthropic.jsonl"),
    ("litellm", "litellm.jsonl"),
    ("claude_code", "claude_code.jsonl"),
]
runner = CliRunner()


def run_adapter(kind: str, name: str, mapper: TaskMapper | None = None) -> tuple[list[SwitchTrace], ImportContext]:
    ctx = ImportContext(path=FIX / name, mapper=mapper or TaskMapper.from_file(FIX / "task_map.yaml"))
    return list(get_adapter(kind).parse(ctx)), ctx


@pytest.mark.parametrize(("kind", "name"), CASES)
def test_golden_output(kind: str, name: str) -> None:
    traces, _ = run_adapter(kind, name)
    got = [json.loads(t.to_jsonl()) for t in traces]
    golden = GOLDEN / f"{name}.golden.jsonl"
    if os.environ.get("GLMKIT_UPDATE_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        golden.write_text("".join(json.dumps(g, sort_keys=True) + "\n" for g in got))
    want = [json.loads(line) for line in golden.read_text().splitlines()]
    assert got == want


@pytest.mark.parametrize(("kind", "name"), CASES)
def test_output_validates_against_switchtrace_schema(kind: str, name: str) -> None:
    schema = json.loads((REPO / "schemas" / "switch-trace-v0.schema.json").read_text())
    traces, _ = run_adapter(kind, name)
    assert traces
    for t in traces:
        jsonschema.validate(json.loads(t.to_jsonl()), schema)
        SwitchTrace.model_validate_json(t.to_jsonl())


def test_every_kind_has_a_fixture() -> None:
    assert {k for k, _ in CASES} == set(adapters())


# ---------------------------------------------------------------- usage normalisation


def _lines(name: str) -> list[dict]:
    out = []
    for line in (FIX / name).read_text().splitlines():
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def test_anthropic_usage_naive_sum_overcounts_adapter_does_not() -> None:
    rows = _lines("anthropic.jsonl")
    usages = [r["response"]["usage"] for r in rows]

    def total(u: dict) -> int:
        cc = u.get("cache_creation") or {}
        write = u.get("cache_creation_input_tokens", sum(cc.values()))
        return u["input_tokens"] + u.get("cache_read_input_tokens", 0) + write

    naive_total = sum(total(u) for u in usages)  # one row per log line: the duplicated msg_01B counts twice
    naive_input_only = sum(u["input_tokens"] for u in usages)  # treats input_tokens as the total: under-counts
    traces, ctx = run_adapter("anthropic", "anthropic.jsonl")
    adapter_total = sum(t.usage.input_tokens_total or 0 for t in traces)
    assert ctx.skipped["duplicate response id"] == 1
    assert (naive_total, adapter_total) == (1220 + 530 + 530, 1750)
    assert naive_total > adapter_total
    assert naive_input_only < adapter_total
    first = traces[0].usage
    assert (first.input_tokens_total, first.cache_read_tokens, first.cache_write_tokens) == (1220, 1000, 200)
    assert first.source_convention == "anthropic"
    # 5m + 1h ephemeral cache creation is summed when the aggregate field is absent
    assert traces[1].usage.cache_write_tokens == 500
    assert traces[1].usage.input_tokens_total == 530


def test_claude_code_usage_naive_per_line_sum_overcounts_adapter_does_not() -> None:
    lines = [r for r in _lines("claude_code.jsonl") if r.get("type") == "assistant"]
    naive_out = sum(r["message"]["usage"]["output_tokens"] for r in lines)
    naive_in = sum(
        r["message"]["usage"].get("input_tokens", 0)
        + r["message"]["usage"].get("cache_read_input_tokens", 0)
        + r["message"]["usage"].get("cache_creation_input_tokens", 0)
        for r in lines
    )
    traces, _ = run_adapter("claude_code", "claude_code.jsonl")
    real_out = sum(t.usage.output_tokens_total or 0 for t in traces)
    real_in = sum(t.usage.input_tokens_total or 0 for t in traces)
    assert len(traces) == 3  # 3 distinct message ids (msg_cc_1 spans 3 lines, <synthetic> dropped)
    assert real_out == 80 + 40 + 12
    assert naive_out > real_out
    assert real_in == (10 + 2000 + 500) + (15 + 2500) + (5 + 100)
    assert naive_in > real_in
    assert traces[0].usage.reasoning_tokens == 30  # counted once, not once per line


def test_claude_code_sidechain_unknown_lines_and_history() -> None:
    traces, ctx = run_adapter("claude_code", "claude_code.jsonl")
    by_id = {t.call_id: t for t in traces}
    assert by_id["msg_cc_sub1"].labels.tags == ["subagent"]
    assert by_id["msg_cc_sub1"].task_type == "subagent-exploration"
    assert by_id["msg_cc_1"].labels.tags == []
    assert ctx.skipped["ignored line type: brand-new-line-type-from-the-future"] == 1
    assert ctx.skipped["invalid JSON line"] == 1
    # blocks of one response merged in order: thinking, then two tool_use
    assert [p.type for p in by_id["msg_cc_1"].response.messages[0].parts] == ["reasoning", "tool_call", "tool_call"]
    # the second call's request contains the first call's full merged assistant message
    assert len(by_id["msg_cc_2"].request.messages[1].parts) == 3
    assert [t.seq for t in traces if not t.labels.tags] == [0, 1]


# ---------------------------------------------------------------- other adapter behaviour


def test_otel_without_content_is_baseline_only_and_non_inference_spans_skipped() -> None:
    traces, ctx = run_adapter("otel", "otel.jsonl")
    assert len(traces) == 3  # s1, s2 and the OTLP-format span; the tool span is skipped
    assert ctx.skipped["non-inference operation: execute_tool"] == 1
    baseline = [t for t in traces if not t.privacy.content_present]
    assert {t.call_id for t in baseline} == {"s2"}
    assert stats_of(traces)["baseline_only"] == 1
    s1 = next(t for t in traces if t.call_id == "s1")
    assert s1.usage.input_tokens_total == 120 and s1.usage.cache_read_tokens == 100  # OTel input includes cache
    assert s1.task_type == "support"


def stats_of(traces: list[SwitchTrace]) -> dict:
    return compute_stats(traces)


def test_litellm_cost_truth_and_no_prompt_rows() -> None:
    traces, _ = run_adapter("litellm", "litellm.jsonl")
    assert [t.cost_usd_reported for t in traces] == [0.0042, 0.0007]
    assert [t.privacy.content_present for t in traces] == [False, True]
    assert traces[0].task_type == "retrieval-qa"
    assert traces[1].usage.cache_read_tokens == 64  # mined from response.usage
    assert traces[0].latency.ttft_ms == 800.0


def test_langfuse_csv_equals_jsonl_semantics_and_cache_conversion() -> None:
    jl, _ = run_adapter("langfuse", "langfuse.jsonl")
    assert [t.call_id for t in jl] == ["g1", "g2"]
    claude = jl[1].usage
    assert (claude.input_tokens_total, claude.cache_read_tokens, claude.cache_write_tokens) == (360, 300, 50)
    assert jl[1].task_type == "writing" and jl[0].task_type == "classification"
    csv_t, _ = run_adapter("langfuse", "langfuse.csv")
    assert csv_t[0].request.messages[0].parts[0].text == "Hi"
    assert csv_t[0].task_type == "chat" and csv_t[0].cost_usd_reported == 0.00002


def test_task_mapper_order_and_unlabelled(tmp_path: Path) -> None:
    m = TaskMapper()
    assert m.assign() == "unlabelled"
    assert m.assign(tags=["x", "y"]) == "x"
    assert m.assign(tags=["x"], meta={"task_type": "qa"}) == "qa"
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"tags": {"x": "mapped"}, "models": {"^gpt": "gpt-work"}, "use_first_tag": False}))
    m2 = TaskMapper.from_file(f)
    assert m2.assign(tags=["x"]) == "mapped"
    assert m2.assign(tags=["z"], model="gpt-4o") == "gpt-work"
    assert m2.assign(tags=["z"], model="other") == "unlabelled"


def test_read_records_bad_json_file_fails_soft(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    ctx = ImportContext(path=p)
    assert list(read_records(ctx)) == []
    assert ctx.skipped["invalid JSON file"] == 1


# ---------------------------------------------------------------- redaction

PLANTED = [
    "ops.lead@example.com",
    "jane.doe@example.org",
    "admin@example.net",
    "415-555-0132",
    "(212) 555-0188",
    "sk-ant-FAKEFAKEFAKEFAKEFAKE0001",
    "ghp_FAKEFAKEFAKEFAKEFAKEFAKE0002",
    "abcDEF0123456789abcdefXYZ",
    "plantedSecretValue99",
    "ACME-CUST-90817",
]


def import_redaction_fixture(redact: bool) -> str:
    traces, _ = run_adapter("openai", "redaction.jsonl")
    red = TraceRedactor([r"ACME-CUST-\d+"]) if redact else None
    return "\n".join((red.trace(t) if red else t).to_jsonl() for t in traces)


def test_redaction_fixture_really_contains_the_secrets() -> None:
    raw = import_redaction_fixture(redact=False)
    assert all(s in raw for s in PLANTED)


def test_redaction_removes_every_planted_secret_and_pii() -> None:
    out = import_redaction_fixture(redact=True)  # a regression here (redaction off or a pattern gap) fails this
    survivors = [s for s in PLANTED if s in out]
    assert survivors == []
    d = json.loads(out)
    assert d["privacy"]["redacted"] is True
    assert d["call_id"] == "chatcmpl-red1"  # identifiers are left alone
    assert d["request"]["tools"][0]["name"] == "lookup"


def test_redaction_does_not_touch_timestamps_or_token_counts() -> None:
    traces, _ = run_adapter("otel", "otel.jsonl")
    before = [t.to_jsonl() for t in traces]
    after = [TraceRedactor().trace(t).model_copy(update={"privacy": t.privacy}).to_jsonl() for t in traces]
    assert before == after


# ---------------------------------------------------------------- CLI, stats, registry


def test_cli_import_stats_and_envelope(tmp_path: Path) -> None:
    app = build_app()
    out = tmp_path / "t.jsonl"
    res = tmp_path / "results"
    r = runner.invoke(
        app,
        [
            "traces",
            "import",
            "--from",
            "anthropic",
            str(FIX / "anthropic.jsonl"),
            "-o",
            str(out),
            "--results",
            str(res),
            "--task-map",
            str(FIX / "task_map.yaml"),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "skipped 1: duplicate response id" in r.output
    assert len(out.read_text().splitlines()) == 2
    env = json.loads(next(res.glob("*/result.json")).read_text())
    assert env["component"] == "traces" and env["data"]["stats"]["total"] == 2
    s = runner.invoke(app, ["traces", "stats", str(out), "--json"])
    assert json.loads(s.output)["tokens_total"]["input_tokens_total"] == 1750
    assert "by source:" in runner.invoke(app, ["traces", "stats", str(out)]).output


def test_cli_unknown_kind_and_missing_file(tmp_path: Path) -> None:
    app = build_app()
    assert runner.invoke(app, ["traces", "import", "--from", "nope", str(FIX / "otel.jsonl")]).exit_code == 2
    assert runner.invoke(app, ["traces", "import", "--from", "otel", str(tmp_path / "x.jsonl")]).exit_code == 2


def test_duplicate_kind_is_rejected() -> None:
    class Dup(Adapter):
        kind = "otel"

    with pytest.raises(ValueError, match="registered twice"):
        register_adapter(Dup)


def test_traces_page_collects_imported_sets(tmp_path: Path) -> None:
    from glmkit.contracts.envelope import ResultEnvelope, new_run_id
    from glmkit.report.pages.traces import TracesPage
    from glmkit.report.registry import RunRef

    app = build_app()
    res = tmp_path / "results"
    for kind, name in (("anthropic", "anthropic.jsonl"), ("litellm", "litellm.jsonl")):
        runner.invoke(
            app,
            ["traces", "import", "--from", kind, str(FIX / name), "-o", str(tmp_path / name), "--results", str(res)],
        )
    refs = [RunRef(ResultEnvelope.model_validate_json(p.read_text()), p.parent.name) for p in res.glob("*/result.json")]
    refs.append(
        RunRef(
            ResultEnvelope(run_id=new_run_id("conform"), component="conform", started_at=refs[0].envelope.started_at),
            "x",
        )
    )
    data = TracesPage().collect(refs)
    assert data["count"] == 2
    assert {s["source"] for s in data["sets"]} == {"anthropic", "litellm"}
    assert data["totals"]["total"] == 4 and data["totals"]["replayable"] == 3
    assert TracesPage().collect([refs[-1]]) is None
