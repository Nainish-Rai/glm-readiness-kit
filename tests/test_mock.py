"""Mock GLM server: documented behaviour on both protocols, scripted rules and per-server/per-request defects."""

from __future__ import annotations

import json

import httpx
import pytest

from glmkit.client.sse import parse_sse
from glmkit.mock import MockConfig, MockServer

OAI = "/api/paas/v4/chat/completions"
PLAN = "/api/coding/paas/v4/chat/completions"
ANT = "/api/anthropic/v1/messages"
HELLO = [{"role": "user", "content": "Reply OK"}]


def post(srv: MockServer, path: str, body: dict, **headers: str) -> httpx.Response:
    return httpx.post(srv.url + path, json=body, headers=headers, timeout=10)


def test_openai_nonstream_shape(mock_server) -> None:
    r = post(mock_server, OAI, {"model": "glm-5.3", "messages": HELLO, "reasoning_effort": "low"})
    assert r.status_code == 200
    b = r.json()
    msg = b["choices"][0]["message"]
    assert (msg["content"], b["choices"][0]["finish_reason"], b["model"]) == ("OK", "stop", "glm-5.3")
    assert msg["reasoning_content"]
    assert set(b["usage"]) >= {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_tokens_details"}


def test_openai_stream_reasoning_then_content_then_usage_and_done(mock_server) -> None:
    r = post(mock_server, OAI, {"model": "glm-5.3", "messages": HELLO, "stream": True})
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = parse_sse(r.text)
    assert evs[-1].data == "[DONE]"
    chunks = [json.loads(e.data) for e in evs[:-1]]
    kinds = [next(iter(c["choices"][0]["delta"]), None) for c in chunks]
    assert kinds[0] == "role" and kinds.index("reasoning_content") < kinds.index("content")
    assert [c["choices"][0]["finish_reason"] for c in chunks].count("stop") == 1
    assert "usage" in chunks[-1] and all("usage" not in c for c in chunks[:-1])


def test_anthropic_nonstream_and_stream(mock_server) -> None:
    body = {"model": "glm-5.3", "max_tokens": 64, "messages": HELLO}
    b = post(mock_server, ANT, body).json()
    assert [c["type"] for c in b["content"]] == ["thinking", "text"] and b["stop_reason"] == "end_turn"
    assert b["content"][0]["signature"]
    evs = parse_sse(post(mock_server, ANT, {**body, "stream": True}).text)
    names = [e.event for e in evs]
    assert names[0] == "message_start" and names[-2:] == ["message_delta", "message_stop"]
    start = json.loads(evs[0].data)
    assert start["message"]["usage"]["input_tokens"] > 0  # documented behaviour; GLM-5#139 is a defect


@pytest.mark.parametrize(
    ("path", "body", "status", "code"),
    [
        (OAI, {"model": "glm-5.3", "messages": HELLO, "thinking": {"type": "disabled"}}, 400, "1210"),
        (ANT, {"model": "glm-5.3", "max_tokens": 9, "messages": HELLO, "thinking": {"type": "disabled"}}, 400, "1210"),
        (OAI, {"model": "glm-9.9", "messages": HELLO}, 400, "1211"),
        (OAI, {"model": "glm-5.3"}, 400, "1213"),
        (OAI, {"messages": HELLO}, 400, "1213"),
        (ANT, {"model": "glm-5.3", "messages": HELLO}, 400, "1213"),
        (OAI, {"model": "glm-5.3", "messages": HELLO, "reasoning_effort": "medium"}, 400, "1214"),
        (OAI, {"model": "glm-5.3", "messages": HELLO, "temperature": 2}, 400, "1214"),
    ],
)
def test_documented_errors(mock_server, path, body, status, code) -> None:
    r = post(mock_server, path, body)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code
    assert r.json()["error"]["message"]


def test_plan_class_converts_disabled_thinking_and_maps_effort(mock_server) -> None:
    r = post(mock_server, PLAN, {"model": "glm-5.3", "messages": HELLO, "thinking": {"type": "disabled"}})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["reasoning_content"]
    assert (
        post(mock_server, PLAN, {"model": "glm-5.3", "messages": HELLO, "reasoning_effort": "medium"}).status_code
        == 200
    )


def test_glm52_effort_none_skips_thinking(mock_server) -> None:
    msg = post(mock_server, OAI, {"model": "glm-5.2", "messages": HELLO, "reasoning_effort": "none"}).json()["choices"][
        0
    ]["message"]
    assert "reasoning_content" not in msg


def test_tool_call_satisfies_top_level_oneof(mock_server) -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "automation_update",
            "parameters": {
                "type": "object",
                "properties": {},
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"mode": {"const": "view"}, "id": {"type": "string"}},
                        "required": ["mode", "id"],
                    }
                ],
            },
        },
    }
    b = post(
        mock_server,
        OAI,
        {"model": "glm-5.3", "messages": [{"role": "user", "content": "set id=23-00 to view"}], "tools": [tool]},
    ).json()
    tc = b["choices"][0]["message"]["tool_calls"][0]
    assert b["choices"][0]["finish_reason"] == "tool_calls"
    assert json.loads(tc["function"]["arguments"]) == {"mode": "view", "id": "23-00"}


def test_tool_stream_fragments_by_index(mock_server) -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "write",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    }
    evs = parse_sse(
        post(
            mock_server,
            OAI,
            {"model": "glm-5.3", "messages": HELLO, "tools": [tool], "stream": True, "tool_stream": True},
        ).text
    )
    frags = [tc for e in evs[:-1] for tc in json.loads(e.data)["choices"][0]["delta"].get("tool_calls", [])]
    assert len(frags) > 2 and all(f["index"] == 0 for f in frags)
    assert json.loads("".join(f["function"].get("arguments", "") for f in frags)) == {
        "path": "example",
        "content": "example",
    }


def test_prefix_cache_reports_cached_tokens(mock_server) -> None:
    body = {"model": "glm-5.3", "messages": [{"role": "system", "content": "x" * 400}, *HELLO]}
    first = post(mock_server, OAI, body).json()["usage"]["prompt_tokens_details"]["cached_tokens"]
    second = post(mock_server, OAI, body).json()["usage"]["prompt_tokens_details"]["cached_tokens"]
    assert first == 0 and second > 0


def test_max_tokens_truncates_with_length(mock_server) -> None:
    b = post(
        mock_server,
        OAI,
        {"model": "glm-5.3", "messages": [{"role": "user", "content": "tell me a long story " * 20}], "max_tokens": 5},
    ).json()
    assert b["choices"][0]["finish_reason"] == "length" and b["usage"]["completion_tokens"] <= 5


def test_scripted_rules_by_match_and_by_scenario_header() -> None:
    rules = [
        {
            "name": "weather",
            "match": {"last_user_regex": "weather"},
            "reply": {"tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}]},
        },
        {
            "name": "throttle",
            "match": {"header": {"x-never": "1"}},
            "error": {"status": 429, "code": "1302", "message": "Rate limit"},
        },
    ]
    with MockServer(MockConfig(rules=rules)) as srv:
        b = post(srv, OAI, {"model": "glm-5.3", "messages": [{"role": "user", "content": "weather?"}]}).json()
        assert b["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"
        r = post(srv, OAI, {"model": "glm-5.3", "messages": HELLO}, **{"X-GLMKit-Scenario": "throttle"})
        assert (r.status_code, r.json()["error"]["code"]) == (429, "1302")


def test_defects_server_wide_per_request_and_none() -> None:
    body = {"model": "glm-5.3", "messages": HELLO, "thinking": {"type": "disabled"}}
    with MockServer(MockConfig()) as srv:
        assert post(srv, OAI, body).status_code == 400
        r = post(srv, OAI, body, **{"X-GLMKit-Defects": "forced-thinking-500"})
        assert r.status_code == 500 and "Traceback" in r.text
        r = post(srv, OAI, body, **{"X-GLMKit-Defects": "no-such-defect"})
        assert r.status_code == 400 and r.headers["x-glmkit-mock-error"] == "1"
    with MockServer(MockConfig(defects=["forced-thinking-500"])) as srv:
        assert post(srv, OAI, body).status_code == 500
        assert post(srv, OAI, body, **{"X-GLMKit-Defects": "none"}).status_code == 400


def test_unknown_server_defect_fails_fast() -> None:
    with pytest.raises(KeyError, match="unknown defect"):
        MockServer(MockConfig(defects=["no-such-defect"]))


def test_require_key_and_count_tokens() -> None:
    with MockServer(MockConfig(require_key="sk-test-123456")) as srv:
        r = post(srv, OAI, {"model": "glm-5.3", "messages": HELLO})
        assert (r.status_code, r.json()["error"]["code"]) == (401, "1001")
        r = post(srv, OAI, {"model": "glm-5.3", "messages": HELLO}, authorization="Bearer wrong")
        assert r.json()["error"]["code"] == "1000"
        assert (
            post(srv, OAI, {"model": "glm-5.3", "messages": HELLO}, authorization="Bearer sk-test-123456").status_code
            == 200
        )
        r = post(srv, ANT + "/count_tokens", {"model": "glm-5.3", "messages": HELLO}, **{"x-api-key": "sk-test-123456"})
        assert r.json()["input_tokens"] > 0


def test_info_lists_defects(mock_server) -> None:
    info = httpx.get(mock_server.url + "/_mock/info").json()
    names = {d["name"] for d in info["defects_available"]}
    assert {"forced-thinking-500", "auth-echo"} <= names
