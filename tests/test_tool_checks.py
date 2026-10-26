"""Regression rows for the tool-call checks, the Wilson helper and the K2VV adapter."""

from __future__ import annotations

import json

import pytest

from glmkit.conform import k2vv
from glmkit.conform.checks import Attempt, CheckContext, get_check
from glmkit.conform.checks.tools import strict_diff, validate_call
from glmkit.conform.stats import f1_scores, wilson_interval
from glmkit.contracts.case import CheckSpec
from glmkit.contracts.evidence import (
    Exchange,
    HttpRequestRecord,
    HttpResponseRecord,
    NormalizedResponse,
    NormToolCall,
)
from glmkit.contracts.profile import get_profile
from tests.test_checks import CASE, CASES

FLAT = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}
ONEOF = {
    "type": "function",
    "function": {
        "name": "upd",
        "parameters": {
            "type": "object",
            "properties": {},
            "oneOf": [{"properties": {"id": {"type": "string"}}, "required": ["id"]}],
        },
    },
}


def call(name="get_weather", args='{"city": "Paris"}', id_="c1", index=0) -> NormToolCall:
    parsed, err = None, None
    try:
        parsed = json.loads(args)
    except ValueError as e:
        err = f"InvalidJson: {e}"
    return NormToolCall(index=index, id=id_, name=name, arguments=args, arguments_parsed=parsed, parse_error=err)


def tex(
    calls, *, tools=(FLAT,), prompt="", content=None, reasoning=None, finish="tool_calls", label="main", body_json=None
):
    msgs = [{"role": "user", "content": prompt}] if prompt else []
    n = NormalizedResponse(
        http_status=200,
        ok=True,
        tool_calls=list(calls),
        content=content,
        reasoning=reasoning,
        finish_reason_norm=finish,
    )
    return Exchange(
        id="e",
        label=label,
        protocol="openai_chat",
        mode="nonstream",
        request=HttpRequestRecord(url="u", body={"model": "glm-5.3", "messages": msgs, "tools": list(tools)}),
        response=HttpResponseRecord(status=200, body_json=body_json),
        normalized=n,
    )


def echo(args: str) -> str:
    return "call get_weather with exactly these arguments: " + args


TWO = [call(id_="a", index=0), call(id_="b", index=1)]
ROWS = [
    ("tool_call_count", tex(TWO), {"equals": 2}, tex([call()]), {"equals": 2}),
    ("tool_calls_valid", tex([call()]), {}, tex([call(args='{"city": 5}')]), {}),
    (
        "tool_arguments_nonempty_when_required",
        tex([call("upd", '{"id": "x"}')], tools=(ONEOF,)),
        {},
        tex([call("upd", "{}")], tools=(ONEOF,)),
        {},
    ),
    (
        "tool_args_echo_prompt",
        tex([call(args='{"city": "Paris", "n": 1}')], prompt=echo('{"city": "Paris", "n": 1}')),
        {},
        tex([call(args='{"city": "Paris", "n": "1"}')], prompt=echo('{"city": "Paris", "n": 1}')),
        {},
    ),
    (
        "tool_arg_startswith",
        tex([call("run", '{"command": "cd /x && ls"}')]),
        {"prefix": "cd /x && "},
        tex([call("run", '{"command": "ls"}')]),
        {"prefix": "cd /x && "},
    ),
    ("no_marker_leak", tex([call()], content="ok"), {}, tex([call()], reasoning="x <tool_call>get</tool_call>"), {}),
    ("tool_ids_unique", tex(TWO), {}, tex([call(id_="a"), call(id_="a", index=1)]), {}),
    (
        "k2vv_scores",
        tex([call()], label="k2vv:0:ref=tool_calls"),
        {"f1_gte": 0.9},
        tex([], finish="stop", label="k2vv:0:ref=tool_calls"),
        {"f1_gte": 0.5},
    ),
]
CASES.extend(ROWS)  # the coverage test in test_checks.py reads this list when it runs


def run(name, exchange, **params) -> str:
    ctx = CheckContext(
        case=CASE,
        target=get_profile("mock"),
        spec=CheckSpec(check=name, **params),
        mode="nonstream",
        exchanges=[exchange],
    )
    return get_check(name).fn(ctx).status


@pytest.mark.parametrize(("name", "good", "gp", "bad", "bp"), ROWS, ids=[r[0] for r in ROWS])
def test_tool_check_passes_and_fails(name, good, gp, bad, bp) -> None:
    assert run(name, good, **gp) == "pass"
    assert run(name, bad, **bp) == "fail"


def test_taxonomy_unknown_name_invalid_json_schema_mismatch() -> None:
    tools = {"get_weather": FLAT["function"]["parameters"]}
    assert validate_call(call("nope"), tools)["error_class"] == "UnknownName"
    assert validate_call(call(args="{not json"), tools)["error_class"] == "InvalidJson"
    assert validate_call(call(args="{}"), tools)["error_class"] == "SchemaMismatch"
    assert validate_call(call(), tools)["error_class"] is None


def test_validators_disagreement_is_reported() -> None:
    # draft 2020-12 `prefixItems` is unknown to draft 7, so the two validators disagree on this call
    schema = {"type": "object", "properties": {"p": {"prefixItems": [{"type": "string"}]}}}
    res = validate_call(call("t", '{"p": [1]}'), {"t": schema})
    assert (res["draft2020"], res["draft7"]) == ("invalid", "valid") and res["error_class"] == "SchemaMismatch"
    out = get_check("tool_calls_valid").fn(
        CheckContext(
            case=CASE,
            target=get_profile("mock"),
            spec=CheckSpec(check="tool_calls_valid"),
            mode="nonstream",
            exchanges=[
                tex(
                    [call("t", '{"p": [1]}')],
                    tools=({"type": "function", "function": {"name": "t", "parameters": schema}},),
                )
            ],
        )
    )
    assert out.status == "fail" and "validators disagree" in out.message


def test_strict_diff_distinguishes_types() -> None:
    assert strict_diff({"a": 1}, {"a": 1}) is None
    assert "expected int" in strict_diff({"a": 1}, {"a": "1"})
    assert strict_diff({"a": 1}, {"a": True}) is not None  # True == 1 in Python, not in JSON
    assert strict_diff({"a": None}, {"a": "None"}) is not None
    assert strict_diff({"a": "x  "}, {"a": "x "}) is not None


def test_wilson_interval_known_values() -> None:
    lo, hi = wilson_interval(19, 20)
    assert (round(lo, 3), round(hi, 3)) == (0.764, 0.991)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_interval(20, 20)[1] == 1.0 and wilson_interval(0, 10)[0] == 0.0
    assert f1_scores(10, 0, 1)[2] == pytest.approx(20 / 21)


def test_pass_rate_wilson_reports_ci_and_gates() -> None:
    attempts = [Attempt(i, [], [], "pass" if i < 19 else "fail") for i in range(20)]

    def r(**p):
        ctx = CheckContext(
            case=CASE,
            target=get_profile("mock"),
            spec=CheckSpec(check="pass_rate_wilson", **p),
            mode="nonstream",
            attempts=attempts,
        )
        return get_check("pass_rate_wilson").fn(ctx)

    out = r(gte=0.9)
    assert out.status == "pass" and out.observed["ci_low"] == pytest.approx(0.764, abs=1e-3) and "Wilson" in out.message
    assert r(gte=0.96).status == "fail"
    assert r(lower_gte=0.8).status == "fail"  # point estimate fine, lower bound not: the CI gate


def test_k2vv_sample_loads_and_scores_against_reference() -> None:
    samples = k2vv.load_corpus(k2vv.resolve_corpus("corpora/k2vv-sample.jsonl"))
    assert len(samples) == 20 and all(s.reference for s in samples)
    assert samples[0].body["messages"][0]["role"] == "system"  # `_input` rewritten
    assert k2vv.canonical(samples[0].body, max_tokens=64)["max_tokens"] == 64


def test_k2vv_reference_file_overrides_line_reference(tmp_path) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}], "reference": {"finish_reason": "stop"}}) + "\n"
    )
    ref = tmp_path / "r.jsonl"
    ref.write_text(json.dumps({"data_index": 0, "finish_reason": "tool_calls"}) + "\n")
    assert k2vv.load_corpus(corpus)[0].reference == "stop"
    assert k2vv.load_corpus(corpus, reference=ref)[0].reference == "tool_calls"
    with pytest.raises(k2vv.CorpusError):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("not json\n")
        k2vv.load_corpus(bad)
