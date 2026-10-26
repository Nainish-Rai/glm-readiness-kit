"""Issue 04: agent-loop checks (pass and fail each), metrics, chunk-split independence, the planted defects against
their cases, soak budget, and the dashboard page."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from glmkit.agentloop import streamfix
from glmkit.agentloop.metrics import cjk_leaks, degeneracy_metrics, is_degenerate, language_of, wilson
from glmkit.conform.checks import CheckContext, get_check
from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.case import CheckSpec, ConformanceCase
from glmkit.contracts.evidence import (
    Exchange,
    HttpRequestRecord,
    NormalizedResponse,
    NormToolCall,
    NormUsage,
)
from glmkit.contracts.profile import get_profile
from tests.conftest import REPO

CASE = ConformanceCase(id="TEST-001", title="t", category="x", severity="info", expect={"default": []})


def ex(
    label="turn0",
    *,
    content="OK",
    reasoning="a short and healthy line of thought",
    calls=(),
    finish="stop",
    prompt=100,
    cached=0,
    completion=10,
    status=200,
    messages=None,
    mode="nonstream",
) -> Exchange:
    n = NormalizedResponse(
        http_status=status,
        ok=status == 200,
        content=content,
        reasoning=reasoning,
        finish_reason_norm=finish,
        finish_reason=finish,
        usage=NormUsage(prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached),
        tool_calls=[
            NormToolCall(index=i, name=nm, arguments=a, arguments_parsed=json.loads(a))
            for i, (nm, a) in enumerate(calls)
        ],
    )
    return Exchange(
        id=label,
        label=label,
        protocol="openai_chat",
        mode=mode,
        request=HttpRequestRecord(url="u", body={"messages": messages or [{"role": "user", "content": "hi"}]}),
        normalized=n,
    )


def run(name: str, exchanges: list[Exchange], case: ConformanceCase = CASE, **params) -> str:
    ctx = CheckContext(
        case=case,
        target=get_profile("mock"),
        spec=CheckSpec(check=name, **params),
        mode="nonstream",
        exchanges=exchanges,
    )
    return get_check(name).fn(ctx).status


EN = [{"role": "user", "content": "list files please"}]
DEGEN = " ".join(["=", "the"] * 30)
FIX = json.loads((REPO / "cases/fixtures/agentloop/typed-args-ok.json").read_text())
BUG = json.loads((REPO / "cases/fixtures/agentloop/typed-args-sglang-35564.json").read_text())

# (check, good exchanges, params, bad exchanges)
ROWS = [
    ("all_turns_ok", [ex()], {}, [ex(), ex("turn1", status=400)]),
    ("agent_turn_has_tool_call", [ex(calls=[("bash", "{}")])], {}, [ex()]),
    ("no_tool_markup_in_text", [ex()], {"scope": "all"}, [ex(reasoning="bash<arg_key>command</arg_key>")]),
    ("reasoning_degeneracy", [ex()], {}, [ex(reasoning=DEGEN)]),
    ("no_replacement_chars", [ex()], {"scope": "all"}, [ex(content="bad � text")]),
    ("no_empty_length_stop", [ex()], {"scope": "all"}, [ex(content="", finish="length")]),
    ("soak_degeneracy_rate", [ex("turn0"), ex("turn1")], {}, [ex("turn0"), ex("turn1", reasoning=DEGEN)]),
    (
        "soak_budget",
        [ex()],
        {},
        [ex("a", completion=150_000), ex("b", completion=150_000), ex("c", completion=150_000)],
    ),
    (
        "cache_usage_field_present",
        [ex(cached=5)],
        {},
        [
            ex().model_copy(
                update={"normalized": ex().normalized.model_copy(update={"usage": NormUsage(prompt_tokens=1)})}
            )
        ],
    ),
    ("cached_tokens_positive", [ex(cached=5)], {}, [ex(cached=0)]),
    (
        "cached_tokens_grow",
        [ex("turn0", prompt=100, cached=0), ex("turn1", prompt=200, cached=80), ex("turn2", prompt=300, cached=250)],
        {},
        [ex("turn0", cached=50), ex("turn1", cached=10)],
    ),
    (
        "prompt_tokens_lower",
        [ex("a", prompt=50), ex("b", prompt=90)],
        {"a": "a", "b": "b"},
        [ex("a", prompt=90), ex("b", prompt=50)],
    ),
    (
        "cached_tokens_not_above",
        [ex("a", cached=1), ex("b", cached=9)],
        {"a": "a", "b": "b"},
        [ex("a", cached=9), ex("b", cached=1)],
    ),
    (
        "usage_parity",
        [ex("nonstream", prompt=100), ex("stream", prompt=100)],
        {},
        [ex("nonstream", prompt=100), ex("stream", prompt=130)],
    ),
    (
        "stream_nonstream_parity",
        [ex("nonstream", calls=[("f", '{"n": 1}')]), ex("stream", calls=[("f", '{"n": 1}')])],
        {},
        [ex("nonstream", calls=[("f", '{"n": 1}')]), ex("stream", calls=[("f", '{"n": "1"}')])],
    ),
    ("tool_stream_fragments", None, {}, None),  # exercised below against the mock
    ("stream_split_independent", None, {}, None),  # exercised below with fixtures
    ("no_cjk_leak", [ex(messages=EN)], {"scope": "all"}, [ex(content="done 日志", messages=EN)]),
    (
        "output_language_matches",
        [ex(content="All files are listed above.", messages=EN)],
        {"scope": "all"},
        [ex(content="好的，已处理完成，请稍等。", messages=EN)],
    ),
    ("identical_outputs", [ex("a"), ex("b")], {}, [ex("a"), ex("b", reasoning="something else entirely here")]),
    ("effort_clamp_observed", [ex("medium"), ex("max")], {}, [ex("medium", status=400), ex("max")]),
    ("prompt_tokens_golden", [ex(prompt=33)], {"golden": {"mock": 33}}, [ex(prompt=48)]),
    ("prompt_tokens_ratio", [ex()], {}, [ex().model_copy(update={"normalized": NormalizedResponse(http_status=200)})]),
]
TESTED = {r[0] for r in ROWS}


@pytest.mark.parametrize(
    ("name", "good", "params", "bad"),
    [r for r in ROWS if r[1] is not None],
    ids=[r[0] for r in ROWS if r[1] is not None],
)
def test_check_passes_and_fails(name, good, params, bad) -> None:
    assert run(name, good, **params) == "pass"
    assert run(name, bad, **params) == "fail"


def test_golden_skips_without_constant() -> None:
    assert run("prompt_tokens_golden", [ex()], golden={"zai": None}) == "skip"


# ------------------------------------------------------------------ metrics


def test_degeneracy_metrics() -> None:
    healthy = "I will inspect the workspace first and then decide which file needs a change before editing anything."
    assert not is_degenerate(degeneracy_metrics(healthy))
    assert is_degenerate(degeneracy_metrics(DEGEN))
    assert is_degenerate(degeneracy_metrics("fine " * 10 + ".0" * 40))
    assert degeneracy_metrics("ok �")["fffd"] == 1


def test_cjk_detector_excludes_quotes_from_inputs() -> None:
    inputs = ["The README title is 日志系统 and the owner wrote こんにちは."]
    assert cjk_leaks("The title is 日志系统.", inputs) == []  # quoted
    assert cjk_leaks("Quoting こんにちは and also 黔.", inputs) == ["黔"]  # one quote, one leak
    assert cjk_leaks("All English here", inputs) == []
    assert cjk_leaks("日志", ["plain English only"]) == ["日志"]
    assert cjk_leaks("Mixed: 日志系统 and 黔", ["only 日志系统 is quoted"]) == ["黔"]


def test_language_of() -> None:
    assert language_of("Everything is fine.") == "en"
    assert language_of("終了コード 0 で完了しました。") == "ja"
    assert language_of("好的，已处理完成。") == "zh"
    assert language_of("작업이 완료되었습니다") == "ko"
    lo, hi = wilson(1, 40)
    assert 0 < lo < 0.025 < hi < 0.15


# ------------------------------------------------------------------ chunk-split independence


@pytest.mark.parametrize("how", ["whole", "char", "token", "bytes3", "bytes7", "pairs"])
def test_every_split_reassembles_to_the_non_stream_result(how) -> None:
    ref = streamfix.reference("openai_chat", FIX["nonstream"])
    text = streamfix.to_sse(FIX["events"])
    got = streamfix.reassemble("openai_chat", text, how, "s")
    from glmkit.conform.checks.agent_loop import parity_diffs

    assert parity_diffs(ref, got) == []


def test_two_split_points_exhaustively_and_interleavings() -> None:
    from glmkit.conform.checks.agent_loop import parity_diffs

    ref = streamfix.reference("openai_chat", FIX["nonstream"])
    text = streamfix.to_sse([*FIX["events"][:12], "[DONE]"])
    base = streamfix.reassemble("openai_chat", text, "whole", "w").normalized
    cuts = range(1, len(text), 37)
    for a, b in itertools.combinations(cuts, 2):
        from glmkit.client.sse import SSEParser

        p = SSEParser()
        evs = p.feed(text[:a]) + p.feed(text[a:b]) + p.feed(text[b:]) + p.close()
        assert len(evs) == len(FIX["events"][:12]) + 1
    assert base.tool_calls  # partial stream still reassembles what it has
    frag = lambda e: next((tc["index"] for c in e.get("choices", []) for tc in c["delta"].get("tool_calls", [])), None)  # noqa: E731
    pos = [i for i, e in enumerate(FIX["events"]) if isinstance(e, dict) and frag(e) is not None]
    sub = [FIX["events"][i] for i in pos]
    for alt in streamfix.interleavings(sub, frag):
        events = list(FIX["events"])
        for i, e in zip(pos, alt, strict=True):
            events[i] = e
        got = streamfix.reassemble("openai_chat", streamfix.to_sse(events), "char", "x")
        assert parity_diffs(ref, got) == []


def test_buggy_fixture_is_detected() -> None:
    from glmkit.conform.checks.agent_loop import parity_diffs

    ref = streamfix.reference("openai_chat", BUG["nonstream"])
    got = streamfix.reassemble("openai_chat", streamfix.to_sse(BUG["events"]), "whole", "b")
    assert any("arguments" in d for d in parity_diffs(ref, got))


# ------------------------------------------------------------------ planted defects vs cases (mock)

DEFECT_CASES = [
    ("reasoning-field-rejected", "THINK-001"),
    ("reasoning-field-rejected", "THINK-003"),
    ("reasoning-collapse", "THINK-005/prose-control"),
    ("stream-args-mismatch", "STRM-001"),
    ("stream-args-mismatch", "STRM-002"),
    ("cjk-injection", "LANG-001"),
    ("cjk-injection", "LANG-002"),
    ("no-cache-hits", "CACHE-001"),
    ("no-cache-hits", "CACHE-002"),
    ("tool-call-in-reasoning", "THINK-001"),
    ("stream-usage-drift", "STRM-005"),
    ("usage-prompt-drift", "USAGE-001"),
    ("nondeterministic-greedy", "THINK-007"),
    ("text-only-accepts-image", "MM-001/text-only-rejects"),
    ("tool-stream-without-stream-500", "STRM-003"),
]


def _run(defects: list[str], pattern: str):
    opts = RunOptions(patterns=[pattern.split("/")[0]], mock_defects=defects, write=False, include_opt_in=True)
    import asyncio

    env = asyncio.run(run_conformance(get_profile("mock"), opts)).envelope
    return {i.id.split("[")[0]: i for i in env.items}


@pytest.mark.parametrize(("defect", "case"), DEFECT_CASES, ids=[f"{d}:{c}" for d, c in DEFECT_CASES])
def test_case_passes_clean_and_fails_on_its_defect(defect: str, case: str) -> None:
    clean = _run([], case)
    assert clean[case].status == "pass", clean[case].reason
    bad = _run([defect], case)
    assert bad[case].status == "fail", (defect, case, bad[case].status)


def test_offline_fixture_cases_and_self_test() -> None:
    items = _run([], "STRM-004") | _run([], "STRM-004B")
    assert items["STRM-004"].status == "pass" and items["STRM-004B"].status == "pass"
    assert len([e for e in items["STRM-004"].evidence if e.label.startswith("split-")]) == 18


def test_soak_is_opt_in_and_budget_capped(monkeypatch) -> None:
    from glmkit.conform.cases import load_cases

    assert "THINK-006" not in {lc.case.id for lc in load_cases(patterns=["THINK-*"])}
    assert "THINK-006" in {lc.case.id for lc in load_cases(patterns=["soak"], include_opt_in=True)}
    monkeypatch.setenv("GLMKIT_SOAK_BUDGET_TOKENS", "200")
    import asyncio

    env = asyncio.run(run_conformance(get_profile("mock"), RunOptions(patterns=["THINK-006"], write=False))).envelope
    first = env.items[0]
    assert first.status == "pass" and 2 <= len(first.evidence) < 20
    monkeypatch.delenv("GLMKIT_SOAK_BUDGET_TOKENS")
    monkeypatch.setenv("GLMKIT_SOAK_MAX_TURNS", "7")
    env = asyncio.run(run_conformance(get_profile("mock"), RunOptions(patterns=["THINK-006"], write=False))).envelope
    assert len(env.items[0].evidence) == 7


def test_all_listed_cases_exist_with_provenance() -> None:
    from glmkit.conform.cases import load_cases

    ids = {lc.case.id: lc.case for lc in load_cases(include_opt_in=True)}
    want = (
        [f"THINK-00{i}" for i in range(1, 9)]
        + [f"STRM-00{i}" for i in range(1, 6)]
        + ["CACHE-001", "CACHE-002", "LANG-001", "LANG-002", "USAGE-001", "MM-001"]
    )
    for i in want:
        c = ids[i]
        assert c.source.issues or c.source.spec or c.source.urls, i


# ------------------------------------------------------------------ dashboard page


def test_agent_loop_page_collects(tmp_path: Path) -> None:
    import asyncio

    from glmkit.report.pages.agent_loop import AgentLoopPage
    from glmkit.report.registry import RunRef

    opts = RunOptions(patterns=["CACHE-002", "STRM-001", "THINK-005"], write=False)
    env = asyncio.run(run_conformance(get_profile("mock"), opts)).envelope
    data = AgentLoopPage().collect([RunRef(envelope=env, path="x/result.json")])
    assert data and data["loops"][0]["turns"][1]["cached"] > 0
    assert data["parity"] and data["trials"]


def test_every_new_case_passes_on_the_clean_mock() -> None:
    import asyncio

    pats = ["THINK-*", "STRM-*", "CACHE-*", "LANG-*", "USAGE-*", "MM-*"]
    env = asyncio.run(run_conformance(get_profile("mock"), RunOptions(patterns=pats, write=False))).envelope
    assert [i.id for i in env.items if i.status not in ("pass", "skip")] == []
