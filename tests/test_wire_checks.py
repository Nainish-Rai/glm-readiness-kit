"""Pass/fail regression rows for the slice-03 ``wire_*`` checks (picked up by tests/test_checks.py)."""

from __future__ import annotations

import json

import pytest

from glmkit.conform.checks import Attempt, CheckContext, get_check
from glmkit.contracts.case import CheckSpec, ConformanceCase
from glmkit.contracts.evidence import (
    Exchange,
    HttpRequestRecord,
    HttpResponseRecord,
    NormalizedResponse,
    NormToolCall,
    NormUsage,
    SSEEventRecord,
    StreamMeta,
)
from glmkit.contracts.profile import get_profile

CASE = ConformanceCase(id="TEST-002", title="t", category="x", severity="info", expect={"default": []})


def ex(
    status=200,
    *,
    label="main",
    body=None,
    req=None,
    mode="nonstream",
    protocol="openai_chat",
    events=None,
    headers=None,
    bytes_=100,
    **n,
):
    norm = NormalizedResponse(http_status=status, ok=status < 300, **n)
    return Exchange(
        id="e",
        label=label,
        protocol=protocol,
        mode=mode,
        request=HttpRequestRecord(url="u", body=req or {"model": "glm-5.3"}, body_bytes=bytes_),
        response=HttpResponseRecord(
            status=status,
            body_json=body,
            body_text=json.dumps(body) if body else "",
            events=events,
            headers=headers or {},
        ),
        normalized=norm,
    )


def usage(p=10, c=5):
    return NormUsage(prompt_tokens=p, completion_tokens=c)


def sse(*datas, event=None):
    return [SSEEventRecord(event=event, data=d) for d in datas]


def run(name, exchanges, **params) -> str:
    ctx = CheckContext(
        case=CASE,
        target=get_profile("mock"),
        spec=CheckSpec(check=name, **params),
        mode="nonstream",
        exchanges=exchanges,
    )
    return get_check(name).fn(ctx).status


def effort(lvl, tokens, i=0):
    return ex(label=f"effort:{lvl}#{i}", usage=usage(10, tokens))


E400 = dict(error_code="1214", error_message="bad")
OPENAI_STREAM_OK = sse(
    {"choices": [{"delta": {"role": "assistant"}}]},
    {"choices": [{"delta": {"reasoning_content": "r"}}]},
    {"choices": [{"delta": {"content": "c"}}]},
    {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 3}},
    "[DONE]",
)
STREAM_BAD = sse(
    {"choices": [{"delta": {"content": "c"}}]},
    {"choices": [{"delta": {"reasoning_content": "r"}}]},
    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    "[DONE]",
)
THROTTLE = dict(error_code="1302", error_message="Retry after 2 seconds")
BODY429 = {"error": {"code": "1302", "message": "Retry after 2 seconds"}}


def tool_req(tc):
    return {"model": "m", "tool_choice": tc}


CHECK_CASES = [
    ("wire_error_code_known", [ex(400, error_code="1210")], {}, [ex(400, error_code="9999")], {}),
    (
        "wire_effort_monotone",
        [effort("low", 10), effort("high", 20), effort("max", 30)],
        {},
        [effort("low", 30), effort("high", 30), effort("max", 30)],
        {},
    ),
    ("wire_unknown_model_rejected", [ex(400, error_code="1211")], {}, [ex(200, model="glm-5.3")], {}),
    (
        "wire_anthropic_error_shape",
        [ex(400, body={"type": "error", "error": {"type": "invalid_request_error", "message": "m"}})],
        {},
        [ex(400, body={"error": {"code": "1"}})],
        {},
    ),
    (
        "wire_large_body_handled",
        [ex(200, usage=usage(200_000), bytes_=1_100_000)],
        {},
        [ex(200, usage=usage(0), bytes_=1_100_000)],
        {},
    ),
    (
        "wire_tool_choice_honored",
        [ex(200, req=tool_req("none"))],
        {},
        [ex(200, req=tool_req("none"), tool_calls=[NormToolCall(name="a")])],
        {},
    ),
    (
        "wire_stop_honoured",
        [ex(200, req={"model": "m", "stop": ["END"]}, content="abc")],
        {},
        [ex(200, req={"model": "m", "stop": ["END"]}, content="abc END def")],
        {},
    ),
    ("wire_json_parseable", [ex(200, content='{"a": 1}')], {}, [ex(200, content='```json\n{"a": 1}\n```')], {}),
    (
        "wire_stream_role_once",
        [ex(mode="stream", stream=StreamMeta(role_events=1))],
        {},
        [ex(mode="stream", stream=StreamMeta(role_events=2))],
        {},
    ),
    (
        "wire_stream_usage_final",
        [ex(mode="stream", events=OPENAI_STREAM_OK)],
        {},
        [ex(mode="stream", events=STREAM_BAD)],
        {},
    ),
    (
        "wire_reasoning_before_content",
        [ex(mode="stream", events=OPENAI_STREAM_OK)],
        {},
        [ex(mode="stream", events=STREAM_BAD)],
        {},
    ),
    (
        "wire_stream_agrees_with_nonstream",
        [ex(label="nonstream", content="abcd", reasoning="xy"), ex(label="stream", content="abcde", reasoning="xyz")],
        {},
        [ex(label="nonstream", content="abcd", reasoning="xy"), ex(label="stream", content="abcde", reasoning=None)],
        {},
    ),
    ("wire_context_overflow_handled", [ex(400, error_code="1261")], {}, [ex(200, finish_reason="stop")], {}),
    (
        "wire_message_start_input_tokens",
        [ex(mode="stream", stream=StreamMeta(first_usage={"input_tokens": 9}))],
        {},
        [ex(mode="stream", stream=StreamMeta(first_usage={"input_tokens": 0, "output_tokens": 1}))],
        {},
    ),
    (
        "wire_thinking_signature",
        [ex(body={"content": [{"type": "thinking", "thinking": "t", "signature": "s"}]})],
        {},
        [ex(body={"content": [{"type": "thinking", "thinking": "t", "signature": ""}]})],
        {},
    ),
    (
        "wire_tool_roundtrip",
        [ex(label="turn1", tool_calls=[NormToolCall(name="a")]), ex(label="turn2")],
        {},
        [ex(label="turn1", tool_calls=[NormToolCall(name="a")]), ex(400, label="turn2", **E400)],
        {},
    ),
    (
        "wire_alias_model_reported",
        [ex(200, req={"model": "opus"}, model="glm-5.3")],
        {},
        [ex(200, req={"model": "opus"}, model="opus")],
        {},
    ),
    (
        "wire_token_ratio",
        [ex(label="corpus:a", usage=usage(100)), ex(label="ref:a", body={"input_tokens": 100})],
        {},
        [ex(label="corpus:a", usage=usage(200)), ex(label="ref:a", body={"input_tokens": 100})],
        {},
    ),
    ("wire_no_5xx_any", [ex(200), ex(429)], {}, [ex(200), ex(500)], {}),
    (
        "wire_no_leak_any",
        [ex(200, body={"ok": 1})],
        {},
        [ex(500, body={"e": "Traceback (most recent call last):"})],
        {},
    ),
    ("wire_cross_key_classified", [ex(429, label="cross:p", error_code="1113")], {}, [ex(500, label="cross:p")], {}),
    (
        "wire_rate_limit_wellformed",
        [ex(200), ex(429, body=BODY429, headers={"retry-after": "2"}, **THROTTLE)],
        {},
        [ex(200), ex(429, body="Too Many Requests")],
        {},
    ),
]


@pytest.mark.parametrize(("name", "good", "gp", "bad", "bp"), CHECK_CASES, ids=[c[0] for c in CHECK_CASES])
def test_wire_check_passes_and_fails(name, good, gp, bad, bp) -> None:
    assert run(name, good, **gp) == "pass"
    assert run(name, bad, **bp) == "fail"


def test_outputs_identical_aggregate() -> None:
    def attempts(*texts):
        return [Attempt(i, [ex(200, content=t)], [], "pass") for i, t in enumerate(texts)]

    def agg(a):
        ctx = CheckContext(
            case=CASE,
            target=get_profile("mock"),
            spec=CheckSpec(check="wire_outputs_identical"),
            mode="nonstream",
            attempts=a,
        )
        return get_check("wire_outputs_identical").fn(ctx).status

    assert agg(attempts("a", "a", "a")) == "pass"
    assert agg(attempts("a", "b", "a")) == "fail"


def test_unknown_token_reference_skips_and_reference_file(tmp_path, monkeypatch) -> None:
    only_main = [ex(label="corpus:a", usage=usage(100))]
    assert run("wire_token_ratio", only_main) == "skip"
    ref = tmp_path / "ref.json"
    ref.write_text('{"a": 50}')
    monkeypatch.setenv("GLMKIT_TOKEN_REFERENCE", str(ref))
    assert run("wire_token_ratio", only_main) == "fail"
