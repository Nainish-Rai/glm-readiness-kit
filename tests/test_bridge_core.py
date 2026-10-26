"""Unit tests of the framework-neutral bridge core (no network)."""

from __future__ import annotations

import copy
import itertools
import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from glmkit.bridge.core import (
    BridgeCore,
    BridgeMiddleware,
    FixEmitter,
    ParseErrorRetry,
    Reject,
    TableErrorClassifier,
    get_upstream,
    upstream_for_endpoint,
)
from glmkit.bridge.core.errors import seconds_until_flush
from glmkit.bridge.core.jsonrepair import ArgumentsUnrepairable, repair_arguments
from glmkit.bridge.core.retry import ToolFailure
from glmkit.bridge.core.schema import flatten_schema, unflatten_args, validation_errors
from glmkit.client.sse import SSEParser

ONEOF = {
    "oneOf": [
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    ]
}


def tool(name: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": "d", "parameters": params}}


def core(flavor: str = "zai") -> BridgeCore:
    return BridgeCore(get_upstream(flavor), emitter=FixEmitter())


def ids(c: BridgeCore) -> list[str]:
    return [e.fix_id for e in c.emitter.events]


# ------------------------------------------------------------------------------------------ request rewriters


@pytest.mark.parametrize("thinking", [{"type": "disabled"}, {"type": "off"}, {"type": False}, False, "none"])
def test_thinking_disabled_becomes_enabled_low(thinking: Any) -> None:
    c = core()
    body = {"model": "glm-5.3", "messages": [], "thinking": thinking}
    out = c.rewrite_request(c.new_context(body), body)
    assert out["thinking"]["type"] == "enabled" and out["reasoning_effort"] == "low"
    assert body["thinking"] == thinking  # input not mutated
    assert ids(c) == ["thinking-disabled-to-low"]


def test_thinking_left_alone_for_non_forcing_model_and_for_plan_upstream() -> None:
    for flavor, model in [("zai", "glm-5.2"), ("zai-plan", "glm-5.3"), ("generic", "glm-5.3")]:
        c = core(flavor)
        body = {"model": model, "messages": [], "thinking": {"type": "disabled"}}
        assert c.rewrite_request(c.new_context(body), body)["thinking"] == {"type": "disabled"}
        assert ids(c) == []


def test_thinking_keeps_explicit_effort() -> None:
    c = core()
    body = {"model": "glm-5.3[1m]", "messages": [], "thinking": {"type": "disabled"}, "reasoning_effort": "high"}
    assert c.rewrite_request(c.new_context(body), body)["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    ("given_", "want"),
    [("medium", "high"), ("none", "low"), ("minimal", "low"), ("xhigh", "max"), ("weird", "max"), ("LOW", "low")],
)
def test_effort_clamp(given_: str, want: str) -> None:
    c = core()
    body = {"model": "glm-5.3", "messages": [], "reasoning_effort": given_}
    assert c.rewrite_request(c.new_context(body), body)["reasoning_effort"] == want


def test_effort_valid_untouched_and_no_event() -> None:
    c = core()
    body = {"model": "glm-5.3", "messages": [], "reasoning_effort": "max"}
    c.rewrite_request(c.new_context(body), body)
    assert ids(c) == []


def test_schema_flatten_and_unflatten_roundtrip() -> None:
    flat, hint = flatten_schema(ONEOF)
    assert set(flat["properties"]) == {"query", "url"} and "required" not in flat and "oneOf" not in flat
    assert "query" in hint and "url" in hint
    assert unflatten_args(ONEOF, {"query": "a", "url": "b"})[0] in ({"query": "a"}, {"url": "b"})
    assert unflatten_args(ONEOF, {"url": "b"}) == ({"url": "b"}, False)
    assert validation_errors(ONEOF, {})


def test_flatten_rewrites_request_and_remembers_original() -> None:
    c = core()
    body = {"model": "glm-5.3", "messages": [], "tools": [tool("lookup", ONEOF)]}
    ctx = c.new_context(body)
    out = c.rewrite_request(ctx, body)
    p = out["tools"][0]["function"]["parameters"]
    assert "oneOf" not in p and ctx.flattened["lookup"] == ONEOF
    assert body["tools"][0]["function"]["parameters"] == ONEOF
    assert ids(c) == ["schema-flatten"]
    # a generic host is not rewritten
    g = core("generic")
    assert "oneOf" in g.rewrite_request(g.new_context(body), body)["tools"][0]["function"]["parameters"]


@pytest.mark.parametrize(
    ("flavor", "present", "after"),
    [
        ("zai", "reasoning_content", {"reasoning_content": "r"}),
        ("cerebras", "reasoning_content", {}),
        ("zai", "reasoning", {"reasoning_content": "r"}),
    ],
)
def test_reasoning_replay_modes(flavor: str, present: str, after: dict[str, str]) -> None:
    c = core(flavor)
    body = {"model": "glm-5.2", "messages": [{"role": "assistant", "content": "x", present: "r"}]}
    m = c.rewrite_request(c.new_context(body), body)["messages"][0]
    assert {k: v for k, v in m.items() if k.startswith("reason")} == after
    assert ("reasoning-replay" in ids(c)) == (flavor == "cerebras" or present == "reasoning")


def test_reasoning_rename_mode() -> None:
    from dataclasses import replace

    up = replace(get_upstream("generic"), reasoning_replay="rename", reasoning_field="reasoning")
    c = BridgeCore(up, emitter=FixEmitter())
    body = {"model": "m", "messages": [{"role": "assistant", "content": "x", "reasoning_content": "r"}]}
    assert c.rewrite_request(c.new_context(body), body)["messages"][0]["reasoning"] == "r"


def test_upstream_for_endpoint() -> None:
    assert upstream_for_endpoint("zai", "api").name == "zai"
    assert upstream_for_endpoint("zai", "plan").name == "zai-plan"
    assert upstream_for_endpoint("mock", "api").name == "zai"
    assert upstream_for_endpoint("openrouter", "third_party").name == "generic"
    assert upstream_for_endpoint("generic", "api", "https://api.cerebras.ai/v1").name == "cerebras"


# ------------------------------------------------------------------------------------------ repair


@pytest.mark.parametrize(
    "raw",
    [
        "{'city': 'Paris', 'ok': True,}",
        json.dumps(json.dumps({"city": "Paris"})),
        '{"city": "Paris"',
        '```json\n{"city": "Paris"}\n```',
        '[{"city": "Paris"}]',
        "{city: 'Paris'}",
        '{"city": "Paris", // note\n}',
    ],
)
def test_repairable_arguments(raw: str) -> None:
    obj, fixes = repair_arguments(raw)
    assert obj["city"] == "Paris" and fixes


@pytest.mark.parametrize("raw", ["", "Action: call get_weather", "[1, 2]", '"just text"', "null", None])
def test_unrepairable_arguments_raise(raw: Any) -> None:
    with pytest.raises(ArgumentsUnrepairable):
        repair_arguments(raw)


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=6),
        st.one_of(st.integers(), st.text(max_size=8), st.booleans(), st.none()),
        max_size=5,
    )
)
def test_valid_json_is_never_changed(obj: dict[str, Any]) -> None:
    out, fixes = repair_arguments(json.dumps(obj))
    assert out == obj and fixes == []


def test_nonstream_response_repair_and_failure() -> None:
    c = core()
    body = {
        "model": "glm-5.3",
        "messages": [],
        "tools": [tool("get_weather", {"type": "object", "properties": {"city": {}}, "required": ["city"]})],
    }
    ctx = c.new_context(body)
    c.rewrite_request(ctx, body)
    resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "1", "function": {"name": "get_weather", "arguments": "{'city': 'Paris'}"}},
                        {"id": "2", "function": {"name": "get_weather", "arguments": "Action: weather"}},
                    ],
                }
            }
        ]
    }
    out, failures = c.rewrite_response(ctx, resp)
    assert out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"city": "Paris"}'
    assert [f.call_id for f in failures] == ["2"] and failures[0].kind == "parse"
    assert "tool-args-repair" in ids(c)


def test_empty_arguments_become_empty_object_only_when_schema_allows() -> None:
    c = core()
    body = {
        "model": "m",
        "messages": [],
        "tools": [
            tool("ping", {"type": "object", "properties": {}}),
            tool("need", {"type": "object", "properties": {"a": {}}, "required": ["a"]}),
        ],
    }
    ctx = c.new_context(body)
    c.rewrite_request(ctx, body)
    resp = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "1", "function": {"name": "ping", "arguments": ""}},
                        {"id": "2", "function": {"name": "need", "arguments": ""}},
                    ]
                }
            }
        ]
    }
    out, failures = c.rewrite_response(ctx, resp)
    assert out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert [f.name for f in failures] == ["need"]


def test_empty_object_for_flattened_oneof_is_a_failure_not_a_pass() -> None:
    c = core()
    body = {"model": "glm-5.3", "messages": [], "tools": [tool("lookup", ONEOF)]}
    ctx = c.new_context(body)
    c.rewrite_request(ctx, body)
    resp = {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "lookup", "arguments": "{}"}}]}}]}
    _, failures = c.rewrite_response(ctx, resp)
    assert failures and failures[0].kind == "schema"


def test_retry_policy_at_most_once() -> None:
    pol = ParseErrorRetry()
    req = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    asst = {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "oops"}}]}
    f = [ToolFailure(0, "c1", "t", "oops", "JSONDecodeError: Expecting value")]
    plan = pol.plan_after_tool_failure(req, asst, f, 0)
    assert plan is not None and plan.request["messages"][-1]["role"] == "tool" and plan.request["stream"] is False
    assert "Expecting value" in plan.request["messages"][-1]["content"]
    assert req["messages"] == [{"role": "user", "content": "hi"}]
    assert pol.plan_after_tool_failure(req, asst, f, 1) is None


def test_fix_events_carry_no_content_unless_enabled() -> None:
    for log_content in (False, True):
        c = BridgeCore(get_upstream("zai"), emitter=FixEmitter(log_content=log_content))
        body = {"model": "m", "messages": [], "tools": [tool("t", {"type": "object", "properties": {"a": {}}})]}
        ctx = c.new_context(body)
        c.rewrite_request(ctx, body)
        c.rewrite_response(
            ctx,
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [{"id": "1", "function": {"name": "t", "arguments": "{'a': 'SECRET'}"}}]
                        }
                    }
                ]
            },
        )
        ev = next(e for e in c.emitter.events if e.fix_id == "tool-args-repair")
        assert "SECRET" not in json.dumps({k: v for k, v in ev.to_dict().items() if k != "content"})
        assert (ev.content is not None) is log_content


# ------------------------------------------------------------------------------------------ errors


@pytest.mark.parametrize(
    ("code", "status", "want", "retryable"),
    [
        ("1302", 429, 429, True),
        ("1305", 429, 503, True),
        ("1308", 429, 429, True),
        ("1310", 429, 429, True),  # issue 19: usage window, resets later (was non-retryable in the bridge table)
        ("1313", 429, 429, True),  # issue 19: Fair Usage Policy limiting clears (was non-retryable)
        ("1321", 429, 429, True),
        ("1113", 429, 402, False),
        ("1309", 429, 402, False),
        ("1311", 429, 403, False),  # issue 19: plan lacks model access is permission/403 (was billing/402)
        ("1315", 429, 403, False),
        ("1211", 400, 404, False),
        ("1210", 400, 400, False),
    ],
)
def test_error_table(code: str, status: int, want: int, retryable: bool) -> None:
    cls = TableErrorClassifier().classify(status, {"error": {"code": code, "message": "x"}})
    assert (cls.status, cls.retryable) == (want, retryable)
    assert (cls.retry_after_s is not None) == retryable


def test_server_codes_retry_without_default_retry_after() -> None:
    cls = TableErrorClassifier().classify(500, {"error": {"code": "1234", "message": "x"}})
    assert (cls.status, cls.retryable, cls.retry_after_s, cls.changed) == (502, True, None, True)


def test_next_flush_time_and_unknown_codes() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
    assert seconds_until_flush("limit reached, next_flush_time: 2026-10-26 13:00:00", now) == 3600
    assert seconds_until_flush("next_flush_time=1793019600", now) == 3600
    assert seconds_until_flush("nothing here") is None
    cls = TableErrorClassifier().classify(500, {"error": {"code": "9999", "message": "boom"}})
    assert cls.status == 500 and cls.retryable and not cls.sanitized
    html = TableErrorClassifier().classify(500, None)
    assert html.sanitized and html.status == 502


def test_classify_error_builds_client_body_and_event() -> None:
    c = core()
    ctx = c.new_context()
    cls, body = c.classify_error(ctx, 429, json.dumps({"error": {"code": "1305", "message": "busy"}}), {})
    assert cls.status == 503 and body["error"]["code"] == "1305" and body["error"]["type"] == "overloaded_error"
    assert ids(c) == ["error-classify"]


# ------------------------------------------------------------------------------------------ middleware


async def test_middleware_hooks_can_reject_and_see_rewritten_request() -> None:
    seen: list[dict[str, Any]] = []

    class Guard(BridgeMiddleware):
        def before_request(self, ctx: Any, body: dict[str, Any]) -> Any:
            seen.append(body)
            return Reject(status=429, message="budget", code="guard_budget") if body.get("max_tokens", 0) > 10 else body

    c = BridgeCore(get_upstream("zai"), emitter=FixEmitter(), middleware=[Guard()])
    body = {"model": "glm-5.3", "messages": [], "thinking": {"type": "disabled"}}
    ctx = c.new_context(body)
    out = await c.before_request(ctx, c.rewrite_request(ctx, body))
    assert isinstance(out, dict) and seen[0]["thinking"]["type"] == "enabled"
    rej = await c.before_request(ctx, {**out, "max_tokens": 99})
    assert isinstance(rej, Reject) and rej.body()["error"]["code"] == "guard_budget"


# ------------------------------------------------------------------------------------------ streaming


def chunk(delta: dict[str, Any], finish: str | None = None, **kw: Any) -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **kw,
    }


def run_stream(
    events: list[Any], tools: list[dict[str, Any]] | None = None, flavor: str = "zai"
) -> tuple[list[Any], Any]:
    c = core(flavor)
    body = {"model": "glm-5.3", "messages": [], "tools": tools or []}
    ctx = c.new_context(body)
    c.rewrite_request(ctx, body)
    rw = c.new_stream(ctx)
    out: list[Any] = []
    for e in events:
        out += rw.feed(copy.deepcopy(e))
    out += rw.close()
    return out, rw


def calls_of(out: list[Any]) -> dict[int, dict[str, str]]:
    acc: dict[int, dict[str, str]] = {}
    for e in out:
        if isinstance(e, dict) and e.get("choices"):
            for tc in e["choices"][0]["delta"].get("tool_calls") or []:
                slot = acc.setdefault(tc["index"], {"id": "", "name": "", "arguments": ""})
                slot["id"] = tc.get("id") or slot["id"]
                slot["name"] += (tc.get("function") or {}).get("name") or ""
                slot["arguments"] += (tc.get("function") or {}).get("arguments") or ""
    return acc


def test_stream_preserves_event_order_and_collapses_tool_args() -> None:
    evs = [
        chunk({"role": "assistant"}),
        chunk({"reasoning_content": "think"}),
        chunk({"content": "Hello "}),
        chunk(
            {"tool_calls": [{"index": 0, "id": "a", "type": "function", "function": {"name": "t", "arguments": ""}}]}
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"x"'}}]}),
        chunk({"content": "world"}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}),
        chunk({}, "tool_calls", usage={"prompt_tokens": 1}),
        "[DONE]",
    ]
    out, _ = run_stream(evs, [tool("t", {"type": "object", "properties": {"x": {}}})])
    kinds = [
        ("done" if e == "[DONE]" else next(iter(e["choices"][0]["delta"])) if e["choices"][0]["delta"] else "finish")
        for e in out
    ]
    assert kinds == ["role", "reasoning_content", "content", "content", "tool_calls", "finish", "done"]
    assert calls_of(out) == {0: {"id": "a", "name": "t", "arguments": '{"x": 1}'}}
    assert out[-2]["usage"] == {"prompt_tokens": 1}


def test_stream_repairs_and_flags_failures() -> None:
    evs = [
        chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "t", "arguments": "{'x': "}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    out, rw = run_stream(evs)
    assert calls_of(out)[0]["arguments"] == '{"x": 1}' and not rw.needs_retry

    bad = [
        chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "t", "arguments": "prose"}}]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    out, rw = run_stream(bad)
    assert rw.needs_retry and out == [] and rw.assistant_message["tool_calls"][0]["function"]["arguments"] == "prose"
    released = rw.resolve([{"id": "a", "type": "function", "function": {"name": "t", "arguments": '{"x": 2}'}}])
    assert calls_of(released)[0]["arguments"] == '{"x": 2}' and released[-1] == "[DONE]"


def test_reasoning_field_is_renamed_in_stream() -> None:
    out, _ = run_stream([chunk({"reasoning": "hm"}), chunk({}, "stop"), "[DONE]"])
    assert out[0]["choices"][0]["delta"] == {"reasoning_content": "hm"}


def fragments(s: str, cuts: list[int]) -> list[str]:
    points = sorted({c % (len(s) + 1) for c in cuts} | {0, len(s)})
    return [s[a:b] for a, b in itertools.pairwise(points) if s[a:b]]


@settings(max_examples=150, deadline=None)
@given(
    args=st.lists(
        st.dictionaries(
            st.sampled_from(["a", "b", "path", "q"]),
            st.one_of(st.integers(), st.text(max_size=12)),
            min_size=1,
            max_size=3,
        ),
        min_size=1,
        max_size=3,
    ),
    cuts=st.lists(st.lists(st.integers(min_value=0, max_value=200), max_size=8), min_size=3, max_size=3),
    order_seed=st.randoms(use_true_random=False),
    byte_cuts=st.lists(st.integers(min_value=1, max_value=60), min_size=1, max_size=40),
)
def test_stream_per_index_buffers_survive_any_split(
    args: list[dict[str, Any]], cuts: list[list[int]], order_seed: Any, byte_cuts: list[int]
) -> None:
    """Arguments split into arbitrary fragments, indices interleaved, bytes re-chunked arbitrarily: each index
    still yields exactly its own arguments, and non-tool events keep their order."""
    pieces: list[tuple[int, str]] = []
    per_index: list[list[str]] = []
    for i, a in enumerate(args):
        frs = fragments(json.dumps(a), cuts[i])
        per_index.append(frs)
    # interleave keeping per-index order
    cursors = [0] * len(per_index)
    while any(cursors[i] < len(per_index[i]) for i in range(len(per_index))):
        i = order_seed.choice([k for k in range(len(per_index)) if cursors[k] < len(per_index[k])])
        pieces.append((i, per_index[i][cursors[i]]))
        cursors[i] += 1
    events: list[Any] = [chunk({"role": "assistant"}), chunk({"content": "one"})]
    for i in range(len(args)):
        events.append(
            chunk(
                {
                    "tool_calls": [
                        {"index": i, "id": f"id{i}", "type": "function", "function": {"name": "t", "arguments": ""}}
                    ]
                }
            )
        )
    for n, (i, frag) in enumerate(pieces):
        events.append(chunk({"tool_calls": [{"index": i, "function": {"arguments": frag}}]}))
        if n == len(pieces) // 2:
            events.append(chunk({"content": "two"}))
    events += [chunk({}, "tool_calls"), "[DONE]"]
    wire = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events).encode()

    # re-chunk the bytes at arbitrary points through the real SSE parser
    parser, parsed, pos, k = SSEParser(), [], 0, 0
    while pos < len(wire):
        step = byte_cuts[k % len(byte_cuts)]
        parsed += parser.feed(wire[pos : pos + step].decode("ascii"))  # json.dumps output is ASCII
        pos, k = pos + step, k + 1
    parsed += parser.close()
    fed = [("[DONE]" if p.data == "[DONE]" else json.loads(p.data)) for p in parsed]

    out, rw = run_stream(fed)
    assert not rw.needs_retry
    got = calls_of(out)
    assert {i: json.loads(c["arguments"]) for i, c in got.items()} == {i: a for i, a in enumerate(args)}
    texts = [
        e["choices"][0]["delta"].get("content")
        for e in out
        if isinstance(e, dict) and e.get("choices") and e["choices"][0]["delta"].get("content")
    ]
    assert texts == ["one", "two"]
    assert out[0]["choices"][0]["delta"] == {"role": "assistant"} and out[-1] == "[DONE]"
    assert out[-2]["choices"][0]["finish_reason"] == "tool_calls"
