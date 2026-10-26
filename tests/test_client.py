"""Unified client: both protocols, stream/non-stream, timings, redaction, transport failures."""

from __future__ import annotations

import pytest

from glmkit.client import GLMClient, MissingKeyError
from glmkit.contracts.profile import get_profile
from glmkit.mock import MockConfig, MockServer
from tests.conftest import profile_at

CANON = {"messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 64}


@pytest.mark.parametrize("name", ["mock", "mock-anthropic"])
@pytest.mark.parametrize("stream", [False, True])
async def test_normalized_response_same_across_protocols_and_modes(mock_server, name, stream) -> None:
    async with GLMClient(profile_at(name, mock_server)) as c:
        ex = await c.send_canonical(CANON, stream=stream)
    n = ex.normalized
    assert (n.ok, n.content, n.finish_reason_norm, n.model) == (True, "OK", "stop", "glm-5.3")
    assert n.reasoning and n.usage and n.usage.prompt_tokens > 0
    assert ex.timing.total_ms is not None and ex.timing.ttfb_ms is not None
    if stream:
        assert ex.timing.ttft_ms is not None and n.stream and n.stream.done_seen and ex.response.events
    else:
        assert ex.response.body_json is not None


async def test_auth_headers_redacted_in_evidence(mock_server, monkeypatch) -> None:
    monkeypatch.setenv("GLMKIT_MOCK_API_KEY", "sk-planted-abcdef123456")
    for name, header in (("mock", "authorization"), ("mock-anthropic", "x-api-key")):
        async with GLMClient(profile_at(name, mock_server)) as c:
            assert c.headers()[header].endswith("sk-planted-abcdef123456")  # really sent
            ex = await c.send_canonical(CANON, stream=False)
        assert ex.request.headers[header] == "[REDACTED]"
        assert "sk-planted" not in ex.model_dump_json()


async def test_connection_refused_is_recorded_not_raised() -> None:
    p = get_profile("mock").with_base_url("http://127.0.0.1:9/api/paas/v4")
    async with GLMClient(p, timeout_s=2) as c:
        ex = await c.send_canonical(CANON, stream=False)
    assert ex.response is None and ex.transport_error and "Connect" in ex.transport_error


async def test_hanging_stream_hits_deadline() -> None:
    rules = [{"name": "slow", "match": {}, "reply": {"content": "slow answer " * 5}, "chunk_delay_s": 0.5}]
    with MockServer(MockConfig(rules=rules, chunk_chars=4)) as srv:
        async with GLMClient(profile_at("mock", srv), stream_timeout_s=0.8) as c:
            ex = await c.send_canonical(CANON, stream=True)
    assert ex.transport_error == "stream_timeout"
    assert ex.normalized.stream and ex.normalized.stream.timed_out and not ex.normalized.stream.done_seen


def test_required_key_missing_raises(monkeypatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    with pytest.raises(MissingKeyError, match="ZAI_API_KEY"):
        GLMClient(get_profile("zai-api"))


def test_canonical_to_anthropic_translation() -> None:
    from glmkit.client.adapters import to_anthropic_body

    canon = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "think",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
        "tools": [{"name": "f", "parameters": {"type": "object"}}],
        "tool_choice": "required",
        "max_tokens": 10,
        "stop": "END",
    }
    b = to_anthropic_body(canon, model="glm-5.3", stream=True)
    assert b["system"] == "sys" and b["stream"] is True and b["stop_sequences"] == ["END"]
    assert b["tool_choice"] == {"type": "any"} and b["tools"][0]["input_schema"] == {"type": "object"}
    assert [blk["type"] for blk in b["messages"][1]["content"]] == ["thinking", "tool_use"]
    assert b["messages"][1]["content"][1]["input"] == {"a": 1}
    assert b["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
    }
